import base64
import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from api.models import Run, RunEvent
from dispatcher import core
from dispatcher.kube import PodInfo


class FakeKube:
    def __init__(self, logs=""):
        self.logs, self.deleted = logs, []

    def read_logs(self, pod_name, limit_bytes):
        return self.logs

    def delete_run(self, run_id):
        self.deleted.append(run_id)


def frame(s, text=b"", **kw):
    d = {"s": s, **kw}
    if text:
        d["b"] = base64.b64encode(text).decode()
    return json.dumps(d)


def make_run(engine, status="running"):
    rid = uuid.uuid4()
    with Session(engine) as s, s.begin():
        s.add(Run(id=rid, language="python3.12", code="x", status=status))
    return str(rid)


def pod(rid, exit_code, reason=None, phase="Succeeded"):
    return PodInfo(f"run-{rid}-abc", rid, phase, "worker-1", datetime.now(UTC), exit_code, reason)


def get(engine, rid):
    with Session(engine) as s:
        return s.get_one(Run, uuid.UUID(rid))


@pytest.mark.parametrize("exit_code,reason,logs,expected", [
    (0, "Completed", frame("out", b"hello\n"), "succeeded"),
    (1, "Error", frame("err", b"boom"), "failed"),
    (124, "Error", frame("meta", timeout=True), "timed_out"),
    (137, "OOMKilled", "", "oom_killed"),
    (None, "DeadlineExceeded", "", "timed_out"),  # layer 2: job deadline fired before the wrapper
    (None, "Evicted", "", "infra_error"),
])
def test_classification_and_cleanup(db, exit_code, reason, logs, expected):
    rid = make_run(db)
    kube = FakeKube(logs)
    core.finalize(db, kube, rid, pod(rid, exit_code, reason, "Failed" if exit_code else "Succeeded"))
    run = get(db, rid)
    assert run.status == expected
    assert run.finished_at is not None and run.node == "worker-1"
    assert kube.deleted == [rid]  # cluster objects removed after the result is stored


def test_output_and_truncation_recorded(db):
    rid = make_run(db)
    logs = "\n".join([frame("out", b"hello\n"), frame("err", b"e"), frame("meta", truncated=True)])
    core.finalize(db, FakeKube(logs), rid, pod(rid, 0, "Completed"))
    run = get(db, rid)
    assert (run.stdout, run.stderr, run.truncated, run.exit_code) == ("hello\n", "e", True, 0)
    with Session(db) as s:
        kinds = {e.kind for e in s.query(RunEvent).filter_by(run_id=uuid.UUID(rid))}
    assert {"stdout", "stderr", "state"} <= kinds


def test_terminal_state_is_never_overwritten(db):
    rid = make_run(db, status="cancelled")
    kube = FakeKube(frame("out", b"late"))
    core.finalize(db, kube, rid, pod(rid, 0, "Completed"))
    assert get(db, rid).status == "cancelled" and get(db, rid).stdout == ""
    assert kube.deleted == [rid]  # still cleaned up


def test_pod_deleted_from_outside_is_infra_error_not_user_failure(db):
    rid = make_run(db)
    p = pod(rid, 143, "Error", "Failed")
    p.deleting = True
    core.finalize(db, FakeKube(""), rid, p)
    run = get(db, rid)
    assert run.status == "infra_error" and run.termination_reason == "pod_deleted"


def test_pod_that_finished_cleanly_is_not_misclassified_by_deletion(db):
    rid = make_run(db)
    p = pod(rid, 0, "Completed", "Succeeded")
    p.deleting = True
    core.finalize(db, FakeKube(""), rid, p)
    assert get(db, rid).status == "succeeded"


@pytest.mark.parametrize("message,expected,reason", [
    ("Pod ephemeral local storage usage exceeds the total limit of containers 128Mi.",
     "failed", "ephemeral_storage_exceeded"),
    ('Usage of EmptyDir volume "tmp" exceeds the limit "64Mi". ',  # real kubelet message, kind v1.36
     "failed", "ephemeral_storage_exceeded"),
    ("The node was low on resource: memory.", "infra_error", "evicted"),
])
def test_eviction_is_user_fault_only_for_storage(db, message, expected, reason):
    rid = make_run(db)
    p = pod(rid, 137, "Error", "Failed")
    p.pod_reason, p.message = "Evicted", message
    core.finalize(db, FakeKube(""), rid, p)
    run = get(db, rid)
    assert (run.status, run.termination_reason) == (expected, reason)


def test_chunks_are_idempotent_and_streamed_before_final_state(db):
    rid = make_run(db)
    part = frame("out", b"one\n") + "\n"
    core._streamed.clear()
    core.persist_chunks(db, rid, part)
    core._streamed.clear()  # a second replica / a restart re-reads from line 0
    core.persist_chunks(db, rid, part + frame("out", b"two\n") + "\n")
    with Session(db) as s:
        texts = [e.data["text"] for e in s.query(RunEvent).filter_by(run_id=uuid.UUID(rid), kind="stdout")
                 .order_by(RunEvent.id)]
    assert texts == ["one\n", "two\n"]  # no duplicates
