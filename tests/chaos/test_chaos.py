"""Chaos: kill components mid-flight and prove the invariants still hold."""
import json
import time
from collections import Counter

import pytest

from tests.e2e.conftest import (
    NS_SBX,
    NS_SYS,
    TERMINAL,
    assert_cleaned_up,
    kubectl,
    wait_status,
    wait_terminal,
)

pytestmark = pytest.mark.chaos

SLOW = "import time\ntime.sleep(5)\nprint('ok')"


def submit(client, code=SLOW, timeout=30):
    r = client.post("/v1/runs", json={"language": "python3.12", "code": code, "timeout_seconds": timeout})
    assert r.status_code == 202, r.text
    return r.json()["id"]


def psql(sql):
    return kubectl("-n", NS_SYS, "exec", "ksc-postgres-0", "--", "psql", "-U", "ksc", "-At", "-c", sql)


def dispatcher_pod():
    return kubectl("-n", NS_SYS, "get", "pod", "-l", "app=ksc-dispatcher", "-o",
                   "jsonpath={.items[0].metadata.uid}")


def restart_count(app):
    return int(kubectl("-n", NS_SYS, "get", "pod", "-l", f"app={app}", "-o",
                       "jsonpath={.items[0].status.containerStatuses[0].restartCount}"))


def sandboxes_empty():
    left = kubectl("-n", NS_SBX, "get", "jobs,pods,configmaps", "-l", "app.kubernetes.io/managed-by=ksc",
                   "-o", "name", check=False)
    assert left == "", left


def wait_sandboxes_empty(timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not kubectl("-n", NS_SBX, "get", "jobs,pods,configmaps", "-l",
                       "app.kubernetes.io/managed-by=ksc", "-o", "name", check=False):
            return
        time.sleep(1)
    sandboxes_empty()


def pods_created_per_job():
    """Job controller SuccessfulCreate events per run: more than one means the run executed twice."""
    ev = json.loads(kubectl("-n", NS_SBX, "get", "events", "-o", "json"))
    return Counter(e["involvedObject"]["name"] for e in ev["items"]
                   if e["reason"] == "SuccessfulCreate" and e["involvedObject"]["kind"] == "Job")


def readyz_status():
    code = ("import urllib.request, urllib.error\n"
            "try:\n r = urllib.request.urlopen('http://127.0.0.1:8080/readyz', timeout=8); print(r.status)\n"
            "except urllib.error.HTTPError as e: print(e.code)\n"
            "except Exception as e: print('ERR')")
    return kubectl("-n", NS_SYS, "exec", "deploy/ksc-api", "--", "python", "-c", code, check=False)


def test_killing_the_dispatcher_with_10_runs_in_flight(client):
    ids = [submit(client) for _ in range(10)]
    deadline = time.time() + 60
    while time.time() < deadline:  # wait until work is genuinely in flight
        if any(client.get(f"/v1/runs/{i}").json()["status"] == "running" for i in ids):
            break
        time.sleep(0.3)
    else:
        pytest.fail("nothing started running")
    before = dispatcher_pod()
    for _ in range(2):  # kill it, let it come back, kill it again
        kubectl("-n", NS_SYS, "delete", "pod", "-l", "app=ksc-dispatcher", "--grace-period=0", "--force",
                check=False)
        time.sleep(4)
    assert dispatcher_pod() != before

    results = {i: wait_terminal(client, i, timeout=240) for i in ids}
    assert all(r["status"] == "succeeded" and r["stdout"] == "ok\n" for r in results.values()), \
        {i: r["status"] for i, r in results.items()}

    id_list = ",".join(f"'{i}'" for i in ids)
    terminal = ",".join(f"'{s}'" for s in sorted(TERMINAL))
    rows = psql(f"select run_id, count(*) from run_events where kind='state' and run_id in ({id_list}) "
                f"and data->>'status' in ({terminal}) group by run_id")
    counts = dict(line.split("|") for line in rows.splitlines())
    assert len(counts) == 10 and set(counts.values()) == {"1"}, counts  # exactly one terminal state each

    created = pods_created_per_job()
    assert all(created[f"run-{i}"] == 1 for i in ids), {i: created[f"run-{i}"] for i in ids}  # none ran twice
    for i in ids:
        assert_cleaned_up(i)
    wait_sandboxes_empty()


def test_database_interruption_degrades_readiness_and_recovers(client):
    rid = submit(client, "import time\ntime.sleep(8)\nprint('survived')")
    wait_status(client, rid, "running")
    api_restarts, disp_restarts = restart_count("ksc-api"), restart_count("ksc-dispatcher")
    assert readyz_status() == "200"

    kubectl("-n", NS_SYS, "scale", "statefulset/ksc-postgres", "--replicas=0")
    try:
        deadline = time.time() + 60
        while readyz_status() != "503":
            assert time.time() < deadline, "readyz never reported 503 with the database down"
            time.sleep(1)
        time.sleep(12)  # the run finishes in the cluster while the database is unreachable
    finally:
        kubectl("-n", NS_SYS, "scale", "statefulset/ksc-postgres", "--replicas=1")

    deadline = time.time() + 120
    while readyz_status() != "200":
        assert time.time() < deadline, "readyz did not recover"
        time.sleep(2)

    run = wait_terminal(client, rid, timeout=120)
    assert run["status"] == "succeeded" and run["stdout"] == "survived\n", run
    again = wait_terminal(client, submit(client, "print('after')"), timeout=90)
    assert again["status"] == "succeeded" and again["stdout"] == "after\n"
    assert restart_count("ksc-api") == api_restarts and restart_count("ksc-dispatcher") == disp_restarts
    assert_cleaned_up(rid)
    wait_sandboxes_empty()
