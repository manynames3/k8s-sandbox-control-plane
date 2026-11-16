"""Regression tests for the findings of the independent review (PROGRESS.md, "Independent review")."""
import base64
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.keys import create
from api.models import Run
from dispatcher import core, logparse
from dispatcher.kube import Obj, PodInfo

NOW = datetime.now(UTC)
T = core.Timing()


def frame(s, raw=b"", **kw):
    d = {"s": s, **kw}
    if raw:
        d["b"] = base64.b64encode(raw).decode()
    return json.dumps(d)


# ------------------------------------------------------------------ NUL bytes and frame floods (finding 1, 2)
def test_nul_bytes_are_replaced_so_postgres_accepts_them(db):
    raw = frame("out", b"a\x00b\n") + "\n"
    parsed = logparse.parse(raw)
    assert "\x00" not in parsed.stdout and parsed.stdout.startswith("a")
    rid = make_run(db, "running")
    core._streamed.clear()
    core.persist_chunks(db, str(rid), raw)  # raised before the fix
    with db.connect() as c:
        stored = c.execute(text("select data->>'text' from run_events where run_id=:i and kind='stdout'"),
                           {"i": rid}).scalar()
    assert stored is not None and "\x00" not in stored


def test_frame_flood_is_batched_and_capped(db):
    rid = make_run(db, "running")
    raw = "\n".join(frame("out", b"x") for _ in range(core.MAX_CHUNK_ROWS + 3000)) + "\n"
    core._streamed.clear()
    core.persist_chunks(db, str(rid), raw)
    with db.connect() as c:
        n = c.execute(text("select count(*) from run_events where run_id=:i and kind='stdout'"), {"i": rid}).scalar()
    assert n == core.MAX_CHUNK_ROWS


def make_run(engine, status, dispatched_ago=60, started=False, job_created=False):
    rid = uuid.uuid4()
    with Session(engine) as s, s.begin():
        s.add(Run(id=rid, language="python3.12", code="x", status=status,
                  dispatched_at=NOW - timedelta(seconds=dispatched_ago),
                  started_at=NOW if started else None,
                  job_created_at=NOW - timedelta(seconds=dispatched_ago) if job_created else None))
    return rid


def status_of(engine, rid):
    with Session(engine) as s:
        return s.get_one(Run, rid).status


class Kube:
    def __init__(self, pods=None, objs=None, fail_delete_for=()):
        self.pods, self.objs, self.fail_delete_for, self.deleted = pods or {}, objs or [], set(fail_delete_for), []

    def list_pods(self):
        return self.pods

    def list_objects(self):
        return self.objs

    def read_logs(self, pod_name, limit_bytes):
        return ""

    def delete_run(self, run_id):
        if run_id in self.fail_delete_for:
            raise RuntimeError("kubernetes api unavailable")
        self.deleted.append(run_id)

    def delete_object(self, kind, name):
        pass


def done_pod(rid):
    return PodInfo(f"run-{rid}-x", str(rid), "Succeeded", "w1", NOW, 0, "Completed")


def test_one_failing_run_cannot_block_the_others_and_is_eventually_failed(db):
    core._failures.clear()
    bad, good = make_run(db, "running", started=True), make_run(db, "running", started=True)
    kube = Kube(pods={str(bad): done_pod(bad), str(good): done_pod(good)}, fail_delete_for=[str(bad)])
    core.observe(db, kube, T)
    assert status_of(db, good) == "succeeded"  # handled although the other run raised
    for _ in range(core.MAX_RUN_FAILURES):  # `bad` keeps failing, but its result was recorded on the first attempt
        core.observe(db, kube, T)
    assert status_of(db, bad) == "succeeded"


def test_a_run_whose_handling_never_succeeds_is_failed_as_infra_error(db):
    core._failures.clear()
    rid = make_run(db, "dispatching", dispatched_ago=999)  # stuck Pending far past the schedule limit

    class Boom(Kube):
        def list_pods(self):
            return {str(rid): PodInfo("p", str(rid), "Pending", None, None, None, None, "ContainerCreating",
                                      NOW - timedelta(seconds=999))}

        def delete_run(self, run_id):
            raise RuntimeError("cannot delete")
    k = Boom()
    for _ in range(core.MAX_RUN_FAILURES):
        core.observe(db, k, T)
    # the state transition is recorded even though cleanup keeps failing, so the slot is released
    assert status_of(db, rid) == "infra_error"


def test_reconcile_runs_even_when_observe_raises(db, monkeypatch):
    rid = make_run(db, "dispatching", dispatched_ago=60)  # no job: reconcile re-queues it
    monkeypatch.setattr(core, "observe", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        core.tick(db, Kube(), cap=0, sandbox=core.podspec.SandboxSettings(), timing=T)
    assert status_of(db, rid) == "queued"


# ------------------------------------------------------------------ never run twice (finding 4)
def test_a_vanished_job_is_never_requeued_once_it_was_created(db):
    created = make_run(db, "dispatching", dispatched_ago=60, job_created=True)
    never = make_run(db, "dispatching", dispatched_ago=60, job_created=False)
    core.reconcile(db, Kube(), T, NOW)
    assert status_of(db, created) == "infra_error"  # it may have run: report it, do not run it again
    assert status_of(db, never) == "queued"  # it never got a Job: safe to retry


def test_failed_job_without_a_pod_is_an_infra_error(db):
    rid = make_run(db, "running", started=True)
    job = Obj("jobs", f"run-{rid}", str(rid), NOW, "BackoffLimitExceeded")
    core.observe(db, Kube(objs=[job]), T, NOW)
    assert status_of(db, rid) == "infra_error"


# ------------------------------------------------------------------ API limits (finding 5, 10)
@pytest.fixture
def client(db):
    from api.main import app
    return TestClient(app)


BODY = {"language": "python3.12", "code": "pass"}


def limit_key(db, rate=600, cap=200):
    k = create("limited")
    with db.begin() as c:
        c.execute(text("update api_keys set rate_limit_per_min=:r, concurrency_cap=:c where name='limited'"),
                  {"r": rate, "c": cap})
    return {"Authorization": "Bearer " + k}


def test_per_key_rate_limit_is_enforced(client, db):
    h = limit_key(db, rate=3)
    assert [client.post("/v1/runs", json=BODY, headers=h).status_code for _ in range(4)] == [202, 202, 202, 429]
    other = {"Authorization": "Bearer " + create("someone-else")}
    assert client.post("/v1/runs", json=BODY, headers=other).status_code == 202  # limits are per key


def test_per_key_in_flight_cap_counts_batches(client, db):
    h = limit_key(db, cap=5)
    batch = {"runs": [BODY] * 4}
    assert client.post("/v1/runs:batch", json=batch, headers=h).status_code == 202
    r = client.post("/v1/runs:batch", json=batch, headers=h)
    assert r.status_code == 429 and "Retry-After" in r.headers
    assert client.post("/v1/runs", json=BODY, headers=h).status_code == 202  # the fifth fits
    assert client.post("/v1/runs", json=BODY, headers=h).status_code == 429


def test_idempotency_key_validation_and_namespaces(client, db):
    h = limit_key(db)
    assert client.post("/v1/runs", json=BODY, headers={**h, "Idempotency-Key": "k" * 201}).status_code == 422
    single = client.post("/v1/runs", json={**BODY, "code": "print(9)"}, headers={**h, "Idempotency-Key": "a:1"})
    batch = client.post("/v1/runs:batch", json={"runs": [BODY, BODY]}, headers={**h, "Idempotency-Key": "a"})
    assert single.status_code == 202 and batch.status_code == 202  # no collision or 409
    assert single.json()["id"] not in [r["id"] for r in batch.json()["runs"]]


def test_oversized_and_unlengthed_bodies_are_refused_before_parsing(client):
    big = client.post("/v1/runs", content=b"x" * (4 * 1024 * 1024 + 1), headers={"Content-Type": "application/json"})
    assert big.status_code == 413
    assert client.get("/healthz").status_code == 200


def test_event_streams_are_capped_per_key(client, db):
    import api.main as m
    h = limit_key(db)
    rid = client.post("/v1/runs", json=BODY, headers=h).json()["id"]
    key_id = next(iter(db.connect().execute(text("select id from api_keys where name='limited'"))))[0]
    m._streams.clear()
    for _ in range(m.MAX_STREAMS_PER_KEY):
        assert m._acquire_stream(key_id)
    assert client.get(f"/v1/runs/{rid}/events", headers=h).status_code == 429
    m._release_stream(key_id)
    with db.begin() as c:
        c.execute(text("update runs set status='succeeded' where id=:i"), {"i": rid})
    assert client.get(f"/v1/runs/{rid}/events", headers=h).status_code == 200  # a slot was free again
    assert m._streams[key_id] == m.MAX_STREAMS_PER_KEY - 1  # and was released when the response finished
    assert client.get(f"/v1/runs/{rid}/events", headers={**h, "Last-Event-ID": "9" * 40}).status_code in (200, 429)
