import time

import pytest

from tests.e2e.conftest import NS_SBX, TERMINAL, assert_cleaned_up, kubectl, wait_terminal

pytestmark = pytest.mark.e2e


def submit_node(client, code, **kw):
    r = client.post("/v1/runs", json={"language": "node20", "code": code, **kw})
    assert r.status_code == 202, r.text
    return r.json()["id"]


def test_node_hello(client):
    rid = submit_node(client, "console.log('hello from node', process.version.startsWith('v20'))")
    run = wait_terminal(client, rid)
    assert run["status"] == "succeeded", run
    assert run["stdout"] == "hello from node true\n"
    assert_cleaned_up(rid)


def test_node_exit_code_stderr_and_stdin(client):
    rid = submit_node(client, "console.error('bad'); process.exit(7)")
    run = wait_terminal(client, rid)
    assert (run["status"], run["exit_code"], run["stderr"]) == ("failed", 7, "bad\n")
    rid = submit_node(client, "console.log(require('fs').readFileSync(0,'utf8').toUpperCase())", stdin="yo")
    run = wait_terminal(client, rid)
    assert run["status"] == "succeeded" and run["stdout"] == "YO\n", run


def test_node_timeout_and_oom(client):
    rid = submit_node(client, "while (true) {}", timeout_seconds=3)
    run = wait_terminal(client, rid)
    assert run["status"] == "timed_out" and run["termination_reason"] == "timeout", run
    rid = submit_node(client, "const b = Buffer.alloc(2 * 1024 ** 3, 1); console.log(b.length)")
    run = wait_terminal(client, rid)
    assert run["status"] == "oom_killed", run
    assert_cleaned_up(rid)


def active_pods():
    out = kubectl("-n", NS_SBX, "get", "pods", "-l", "app.kubernetes.io/managed-by=ksc",
                  "-o", "jsonpath={range .items[*]}{.status.phase}{\"\\n\"}{end}", check=False)
    return sum(1 for p in out.split() if p in ("Pending", "Running"))


def cap():
    return int(kubectl("-n", NS_SBX, "get", "resourcequota", "sandbox-quota", "-o",
                       "jsonpath={.spec.hard.pods}"))


def test_batch_of_20_respects_the_cap_at_every_sample(client):
    limit = cap()
    body = {"runs": [{"language": "python3.12" if i % 2 else "node20",
                      "code": f"print({i})" if i % 2 else f"console.log({i})"} for i in range(20)]}
    r = client.post("/v1/runs:batch", json=body)
    assert r.status_code == 202, r.text
    ids = [x["id"] for x in r.json()["runs"]]
    peak, deadline, done = 0, time.time() + 300, {}
    while len(done) < 20 and time.time() < deadline:
        peak = max(peak, active_pods())
        assert peak <= limit, f"{peak} sandbox pods active, cap is {limit}"
        for i in ids:
            if i not in done:
                run = client.get(f"/v1/runs/{i}").json()
                if run["status"] in TERMINAL:
                    done[i] = run
    assert len(done) == 20, f"only {len(done)} finished"
    assert all(d["status"] == "succeeded" for d in done.values()), \
        {i: d["status"] for i, d in done.items() if d["status"] != "succeeded"}
    assert [d["stdout"].strip() for d in (done[i] for i in ids)] == [str(n) for n in range(20)]
    assert peak >= 2, "the queue was never exercised"
    for i in ids:
        assert_cleaned_up(i)
