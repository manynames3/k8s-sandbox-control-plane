import pytest

from tests.e2e.conftest import assert_cleaned_up, wait_terminal

pytestmark = pytest.mark.e2e


def submit(client, code, **kw):
    r = client.post("/v1/runs", json={"language": "python3.12", "code": code, **kw})
    assert r.status_code == 202, r.text
    return r.json()["id"]


def test_hello_succeeds_and_is_cleaned_up(client):
    rid = submit(client, "print('hello')")
    run = wait_terminal(client, rid)
    assert run["status"] == "succeeded", run
    assert run["exit_code"] == 0
    assert run["stdout"] == "hello\n" and run["stderr"] == "" and run["truncated"] is False
    assert run["node"] and run["started_at"] and run["finished_at"]
    assert_cleaned_up(rid)
