import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from api.keys import create


@pytest.fixture
def client(db):
    from api.main import app
    return TestClient(app)


@pytest.fixture
def key(db):
    return {"Authorization": "Bearer " + create("l")}


def submit(client, key, n, language="python3.12"):
    return [client.post("/v1/runs", json={"language": language, "code": f"print({i})"},
                        headers=key).json()["id"] for i in range(n)]


def test_list_is_newest_first_with_stable_cursor_pagination(client, key):
    ids = submit(client, key, 7)
    first = client.get("/v1/runs?limit=3", headers=key).json()
    assert [r["id"] for r in first["runs"]] == ids[::-1][:3] and first["next_cursor"]
    submit(client, key, 2)  # new runs arriving between pages must not shift the next page
    second = client.get(f"/v1/runs?limit=3&cursor={first['next_cursor']}", headers=key).json()
    assert [r["id"] for r in second["runs"]] == ids[::-1][3:6]
    last = client.get(f"/v1/runs?limit=3&cursor={second['next_cursor']}", headers=key).json()
    assert [r["id"] for r in last["runs"]] == ids[::-1][6:] and last["next_cursor"] is None


def test_list_filters_and_hides_code_and_output(client, key, db):
    a, b = submit(client, key, 1)[0], submit(client, key, 1, "node20")[0]
    with db.begin() as c:
        c.execute(text("update runs set status='succeeded' where id=:i"), {"i": a})
    assert [r["id"] for r in client.get("/v1/runs?status=succeeded", headers=key).json()["runs"]] == [a]
    assert [r["id"] for r in client.get("/v1/runs?language=node20", headers=key).json()["runs"]] == [b]
    row = client.get("/v1/runs", headers=key).json()["runs"][0]
    assert "code" not in row and "stdout" not in row


@pytest.mark.parametrize("qs", ["limit=0", "limit=101", "status=bogus", "cursor=%%%"])
def test_list_rejects_bad_parameters(client, key, qs):
    assert client.get(f"/v1/runs?{qs}", headers=key).status_code == 422


def test_list_is_private_to_the_key(client, key):
    submit(client, key, 2)
    other = {"Authorization": "Bearer " + create("other-l")}
    assert client.get("/v1/runs", headers=other).json()["runs"] == []


def test_cluster_snapshot_combines_live_counts_and_dispatcher_nodes(client, key, db, monkeypatch):
    monkeypatch.setenv("CONCURRENCY_CAP", "4")
    submit(client, key, 3)
    with db.begin() as c:
        c.execute(text("update runs set status='running' where id=(select id from runs limit 1)"))
        c.execute(text("insert into cluster_state (id, data) values (1, "
                       "'{\"nodes\": [{\"name\": \"w1\", \"active_sandboxes\": 1}, "
                       "{\"name\": \"w2\", \"active_sandboxes\": 0}]}')"))
    c = client.get("/v1/cluster", headers=key).json()
    assert (c["concurrency_cap"], c["queued"], c["running"], c["free_slots"]) == (4, 2, 1, 3)
    assert c["nodes"] == [{"name": "w1", "active_sandboxes": 1}, {"name": "w2", "active_sandboxes": 0}]
    assert c["snapshot_age_seconds"] is not None and c["snapshot_age_seconds"] < 60


def test_cluster_without_a_snapshot_is_still_served(client, key):
    c = client.get("/v1/cluster", headers=key).json()
    assert c["nodes"] == [] and c["snapshot_age_seconds"] is None


def test_cors_allows_the_local_ui_only(client):
    ok = client.options("/v1/runs", headers={"Origin": "http://localhost:5173",
                                             "Access-Control-Request-Method": "POST",
                                             "Access-Control-Request-Headers": "authorization"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = client.options("/v1/runs", headers={"Origin": "https://evil.example",
                                              "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in bad.headers
