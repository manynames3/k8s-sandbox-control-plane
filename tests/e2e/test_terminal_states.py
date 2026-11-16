"""Every terminal state, reached through the public API on the real cluster, each followed by the
cleanup proof (Job, Pod and ConfigMap gone within a bounded wait)."""
import time
from datetime import datetime

import pytest

from tests.e2e.conftest import assert_cleaned_up, kubectl, wait_status, wait_terminal

pytestmark = pytest.mark.e2e


def submit(client, code, **kw):
    r = client.post("/v1/runs", json={"language": "python3.12", "code": code, **kw})
    assert r.status_code == 202, r.text
    return r.json()["id"]


def secs(a: str, b: str) -> float:
    return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()


def test_nonzero_exit_is_failed_and_keeps_exit_code_and_stderr(client):
    rid = submit(client, "import sys\nprint('out')\nprint('err', file=sys.stderr)\nsys.exit(3)")
    run = wait_terminal(client, rid)
    assert (run["status"], run["exit_code"]) == ("failed", 3), run
    assert run["stdout"] == "out\n" and run["stderr"] == "err\n"
    assert_cleaned_up(rid)


def test_uncaught_exception_is_failed_with_traceback(client):
    rid = submit(client, "raise ValueError('boom')")
    run = wait_terminal(client, rid)
    assert run["status"] == "failed" and run["exit_code"] == 1
    assert "ValueError: boom" in run["stderr"]
    assert_cleaned_up(rid)


def test_stdin_is_delivered(client):
    rid = submit(client, "print(input().upper())", stdin="hi\n")
    run = wait_terminal(client, rid)
    assert run["status"] == "succeeded" and run["stdout"] == "HI\n"
    assert_cleaned_up(rid)


def test_infinite_loop_times_out_at_about_the_timeout(client):
    rid = submit(client, "while True:\n    pass", timeout_seconds=3)
    run = wait_terminal(client, rid)
    assert run["status"] == "timed_out" and run["termination_reason"] == "timeout", run
    # layer 1 (the wrapper) fired, not the 33 s job-deadline backstop
    assert secs(run["started_at"], run["finished_at"]) < 3 + 8
    assert_cleaned_up(rid)


def test_memory_past_the_limit_is_oom_killed(client):
    rid = submit(client, "x = b'x' * (2 * 1024 ** 3)\nprint(len(x))", timeout_seconds=30)
    run = wait_terminal(client, rid)
    assert run["status"] == "oom_killed", run
    assert_cleaned_up(rid)


def test_cancel_a_running_run(client):
    rid = submit(client, "import time\nwhile True:\n    time.sleep(1)", timeout_seconds=120)
    wait_status(client, rid, "running")
    r = client.delete(f"/v1/runs/{rid}")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert_cleaned_up(rid)
    assert client.get(f"/v1/runs/{rid}").json()["status"] == "cancelled"  # never overwritten


def test_deleting_the_job_mid_run_is_an_infra_error(client):
    rid = submit(client, "import time\nwhile True:\n    time.sleep(1)", timeout_seconds=120)
    wait_status(client, rid, "running")
    kubectl("-n", "sandboxes", "delete", "job", f"run-{rid}", "--wait=false")
    run = wait_terminal(client, rid, timeout=60)
    assert run["status"] == "infra_error", run
    assert_cleaned_up(rid)


def test_idempotency_key_runs_exactly_once(client):
    h = {"Idempotency-Key": f"once-{time.time()}"}
    body = {"language": "python3.12", "code": "print('once')"}
    a = client.post("/v1/runs", json=body, headers=h).json()["id"]
    b = client.post("/v1/runs", json=body, headers=h).json()["id"]
    assert a == b
    run = wait_terminal(client, a)
    assert run["status"] == "succeeded" and run["stdout"] == "once\n"
    assert_cleaned_up(a)
