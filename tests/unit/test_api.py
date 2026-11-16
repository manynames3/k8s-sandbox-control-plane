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
    {"code": "x" * (64 * 1024 + 1)}, {"language": "node20"}, {"language": "ruby"},
])
def test_validation(client, key, patch):
    assert client.post("/v1/runs", json={**BODY, **patch}, headers=key).status_code == 422


def test_runs_are_private_to_their_key(client, key, db):
    rid = client.post("/v1/runs", json=BODY, headers=key).json()["id"]
    other = {"Authorization": "Bearer " + create("other")}
    assert client.get(f"/v1/runs/{rid}", headers=other).status_code == 404
