"""KEDA scales dispatcher replicas on queue depth, and the system stays correct with several
dispatchers running at once: the cap holds and no run executes twice."""
import time

import pytest

from tests.chaos.test_chaos import pods_created_per_job
from tests.e2e.conftest import NS_SBX, NS_SYS, TERMINAL, assert_cleaned_up, kubectl

pytestmark = pytest.mark.e2e


def replicas():
    out = kubectl("-n", NS_SYS, "get", "deploy", "ksc-dispatcher", "-o", "jsonpath={.status.readyReplicas}",
                  check=False)
    return int(out or 0)


def active_pods():
    out = kubectl("-n", NS_SBX, "get", "pods", "-l", "app.kubernetes.io/managed-by=ksc", "-o",
                  "jsonpath={range .items[*]}{.status.phase}{\"\\n\"}{end}", check=False)
    return sum(1 for p in out.split() if p in ("Pending", "Running"))


def test_dispatchers_scale_out_on_queue_depth_stay_correct_and_scale_back(client):
    cap = int(kubectl("-n", NS_SBX, "get", "resourcequota", "sandbox-quota", "-o", "jsonpath={.spec.hard.pods}"))
    body = {"runs": [{"language": "python3.12", "code": f"import time\ntime.sleep(4)\nprint({i})"}
                     for i in range(20)]}
    ids = []
    for _ in range(2):  # 40 runs queued at once
        ids += [r["id"] for r in client.post("/v1/runs:batch", json=body).json()["runs"]]
    peak_replicas, peak_pods, done = replicas(), 0, {}
    deadline = time.time() + 420
    while len(done) < len(ids) and time.time() < deadline:
        peak_replicas = max(peak_replicas, replicas())
        peak_pods = max(peak_pods, active_pods())
        assert peak_pods <= cap, f"{peak_pods} sandbox pods active with several dispatchers, cap is {cap}"
        for i in ids:
            if i not in done:
                run = client.get(f"/v1/runs/{i}").json()
                if run["status"] in TERMINAL:
                    done[i] = run
    assert len(done) == len(ids), f"only {len(done)}/{len(ids)} finished"
    assert all(d["status"] == "succeeded" for d in done.values()), \
        {i: d["status"] for i, d in done.items() if d["status"] != "succeeded"}
    assert peak_replicas >= 2, f"KEDA never scaled the dispatcher out (peak {peak_replicas})"
    created = pods_created_per_job()
    assert all(created[f"run-{i}"] == 1 for i in ids), "a run executed more than once"
    for i in ids[:5]:
        assert_cleaned_up(i)

    deadline = time.time() + 180  # cooldown (30 s) plus the scale-down itself
    while replicas() > 1 and time.time() < deadline:
        time.sleep(3)
    assert replicas() == 1, "dispatcher did not scale back to 1 after the queue drained"
