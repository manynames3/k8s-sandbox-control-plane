import pytest
from fastapi.testclient import TestClient

from api.keys import create


@pytest.fixture
def client(db):
    from api.main import app
    return TestClient(app)


def test_cancel_queued_run_and_idempotent(client, db):
    h = {"Authorization": "Bearer " + create("c")}
    rid = client.post("/v1/runs", json={"language": "python3.12", "code": "pass"}, headers=h).json()["id"]
    r = client.delete(f"/v1/runs/{rid}", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert r.json()["finished_at"] is not None
    assert client.delete(f"/v1/runs/{rid}", headers=h).json()["status"] == "cancelled"


def test_cannot_cancel_other_keys_run_or_finished_run(client, db):
    h = {"Authorization": "Bearer " + create("a")}
    other = {"Authorization": "Bearer " + create("b")}
    rid = client.post("/v1/runs", json={"language": "python3.12", "code": "pass"}, headers=h).json()["id"]
    assert client.delete(f"/v1/runs/{rid}", headers=other).status_code == 404
    from sqlalchemy import text
    with db.begin() as c:
        c.execute(text("update runs set status='succeeded' where id=:i"), {"i": rid})
    assert client.delete(f"/v1/runs/{rid}", headers=h).json()["status"] == "succeeded"
