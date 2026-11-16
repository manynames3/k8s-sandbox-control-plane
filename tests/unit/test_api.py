import pytest
from fastapi.testclient import TestClient

from api.keys import create


@pytest.fixture
def client(db):
    from api.main import app
    return TestClient(app)


@pytest.fixture
def key(db):
    return {"Authorization": "Bearer " + create("t")}


BODY = {"language": "python3.12", "code": "print('hello')"}


def test_requires_auth(client):
    assert client.post("/v1/runs", json=BODY).status_code == 401
    bad = {"Authorization": "Bearer nope"}
    assert client.post("/v1/runs", json=BODY, headers=bad).status_code == 401


def test_create_and_get(client, key):
    r = client.post("/v1/runs", json=BODY, headers=key)
    assert r.status_code == 202 and r.json()["status"] == "queued"
    g = client.get(f"/v1/runs/{r.json()['id']}", headers=key)
    assert g.status_code == 200 and g.json()["id"] == r.json()["id"]


def test_keys_stored_hashed(db):
    from sqlalchemy import text
    k = create("h")
    with db.connect() as c:
        stored = c.execute(text("select key_hash from api_keys where name='h'")).scalar()
    assert k not in stored and len(stored) == 64


def test_idempotency(client, key):
    h = {**key, "Idempotency-Key": "abc"}
    a = client.post("/v1/runs", json=BODY, headers=h)
    b = client.post("/v1/runs", json=BODY, headers=h)
    assert a.json()["id"] == b.json()["id"]
    c = client.post("/v1/runs", json={**BODY, "code": "print(2)"}, headers=h)
    assert c.status_code == 409


@pytest.mark.parametrize("patch", [
    {"code": ""}, {"timeout_seconds": 0}, {"timeout_seconds": 121},
    {"code": "x" * (64 * 1024 + 1)}, {"language": "ruby"},
])
def test_validation(client, key, patch):
    assert client.post("/v1/runs", json={**BODY, **patch}, headers=key).status_code == 422


def test_runs_are_private_to_their_key(client, key, db):
    rid = client.post("/v1/runs", json=BODY, headers=key).json()["id"]
    other = {"Authorization": "Bearer " + create("other")}
    assert client.get(f"/v1/runs/{rid}", headers=other).status_code == 404


def test_batch_creates_all_and_is_idempotent(client, key):
    body = {"runs": [{"language": "python3.12", "code": f"print({i})"} for i in range(3)]}
    h = {**key, "Idempotency-Key": "b1"}
    a = client.post("/v1/runs:batch", json=body, headers=h)
    assert a.status_code == 202 and len(a.json()["runs"]) == 3
    b = client.post("/v1/runs:batch", json=body, headers=h)
    assert [r["id"] for r in a.json()["runs"]] == [r["id"] for r in b.json()["runs"]]


@pytest.mark.parametrize("n", [0, 21])
def test_batch_size_limits(client, key, n):
    body = {"runs": [{"language": "python3.12", "code": "pass"}] * n}
    assert client.post("/v1/runs:batch", json=body, headers=key).status_code == 422


def test_batch_is_atomic_on_a_bad_member(client, key, db):
    from sqlalchemy import text
    body = {"runs": [{"language": "python3.12", "code": "pass"},
                     {"language": "python3.12", "code": "x" * (64 * 1024 + 1)}]}
    assert client.post("/v1/runs:batch", json=body, headers=key).status_code == 422
    with db.connect() as c:
        assert c.execute(text("select count(*) from runs")).scalar() == 0


def test_node_is_enabled(client, key):
    r = client.post("/v1/runs", json={"language": "node20", "code": "console.log(1)"}, headers=key)
    assert r.status_code == 202


def test_sse_streams_state_and_log_events_then_ends(client, key, db):
    from sqlalchemy import text
    rid = client.post("/v1/runs", json=BODY, headers=key).json()["id"]
    with db.begin() as c:
        c.execute(text("insert into run_events (run_id, kind, data) values (:r, 'stdout', "
                       "'{\"text\": \"hello\\\\n\", \"line\": 0}')"), {"r": rid})
        c.execute(text("update runs set status='succeeded' where id=:r"), {"r": rid})
        c.execute(text("insert into run_events (run_id, kind, data) values (:r, 'state', "
                       "'{\"status\": \"succeeded\"}')"), {"r": rid})
    r = client.get(f"/v1/runs/{rid}/events", headers=key)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    kinds = [ln.split(": ", 1)[1] for ln in r.text.splitlines() if ln.startswith("event: ")]
    assert kinds == ["state", "stdout", "state", "end"]  # queued, log, succeeded, end


def test_sse_resumes_after_last_event_id_and_is_private(client, key, db):
    rid = client.post("/v1/runs", json=BODY, headers=key).json()["id"]
    from sqlalchemy import text
    with db.begin() as c:
        c.execute(text("update runs set status='failed' where id=:r"), {"r": rid})
    first = client.get(f"/v1/runs/{rid}/events", headers=key).text
    last_id = [ln for ln in first.splitlines() if ln.startswith("id: ")][-1].split(": ")[1]
    again = client.get(f"/v1/runs/{rid}/events", headers={**key, "Last-Event-ID": last_id}).text
    assert "event: state" not in again and "event: end" in again
    other = {"Authorization": "Bearer " + create("o2")}
    assert client.get(f"/v1/runs/{rid}/events", headers=other).status_code == 404
