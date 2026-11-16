#!/usr/bin/env python3
"""Benchmark: N short CPU-bound runs at fixed client concurrency, timings taken from the API's own
timestamps. Writes bench/results/<date>-<env>.json with full provenance.

  uv run python bench/bench.py --runs 200 --concurrency 5 20        # local kind (port-forwards itself)
  BENCH_ENV=github-runner uv run python bench/bench.py ...          # label for another environment
"""
import argparse
import json
import math
import os
import platform
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import httpx

CODE = "print(sum(i * i for i in range(1_500_000)))"  # ~0.2 s of CPU
TERMINAL = {"succeeded", "failed", "timed_out", "oom_killed", "cancelled", "infra_error"}


def sh(*cmd: str) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout.strip()


def pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return round(s[max(math.ceil(p / 100 * len(s)) - 1, 0)], 3)


def summary(values: list[float]) -> dict[str, float | None]:
    return {"p50": pct(values, 50), "p95": pct(values, 95), "p99": pct(values, 99), "max": pct(values, 100)}


def ts(s: str | None) -> float | None:
    return datetime.fromisoformat(s).timestamp() if s else None


def one_run(client: httpx.Client) -> dict:
    r = client.post("/v1/runs", json={"language": "python3.12", "code": CODE, "timeout_seconds": 30})
    r.raise_for_status()
    rid = r.json()["id"]
    while True:
        run = client.get(f"/v1/runs/{rid}").json()
        if run["status"] in TERMINAL:
            return run
        time.sleep(0.2)


def run_level(base: str, key: str, runs: int, concurrency: int) -> dict:
    local = threading.local()

    def worker(_: int) -> dict:
        if not hasattr(local, "c"):
            local.c = httpx.Client(base_url=base, headers={"Authorization": f"Bearer {key}"}, timeout=30)
        return one_run(local.c)

    t0 = time.time()
    with ThreadPoolExecutor(concurrency) as ex:
        results = list(ex.map(worker, range(runs)))
    wall = time.time() - t0
    ok = [r for r in results if r["status"] == "succeeded"]
    queue, sched, e2e, exec_ = [], [], [], []
    for r in ok:
        c, d, s, f = (ts(r[k]) for k in ("created_at", "dispatched_at", "started_at", "finished_at"))
        if None in (c, d, s, f):
            continue
        queue.append(d - c)
        sched.append(s - d)
        exec_.append(f - s)
        e2e.append(f - c)
    statuses: dict[str, int] = {}
    for r in results:
        statuses[r["status"]] = statuses.get(r["status"], 0) + 1
    return {
        "concurrency": concurrency, "sample_size": runs, "statuses": statuses,
        "infra_error_ratio": round(statuses.get("infra_error", 0) / runs, 4),
        "wall_seconds": round(wall, 1), "throughput_runs_per_second": round(runs / wall, 3),
        "queue_wait_seconds": summary(queue), "schedule_seconds": summary(sched),
        "exec_seconds": summary(exec_), "e2e_seconds": summary(e2e),
    }


def provenance(env: str) -> dict:
    nodes = json.loads(sh("kubectl", "get", "nodes", "-o", "json") or '{"items": []}')["items"]
    workers = [n for n in nodes if "node-role.kubernetes.io/control-plane" not in n["metadata"].get("labels", {})]
    info = nodes[0]["status"]["nodeInfo"] if nodes else {}
    quota = sh("kubectl", "-n", "sandboxes", "get", "resourcequota", "sandbox-quota", "-o", "jsonpath={.spec.hard.pods}")
    colima = sh("colima", "list", "--json")
    vm = json.loads(colima.splitlines()[0]) if colima else {}
    return {
        "date": datetime.now(UTC).strftime("%Y-%m-%d"), "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        "environment": env, "git_sha": sh("git", "rev-parse", "--short", "HEAD"),
        "git_dirty": bool(sh("git", "status", "--porcelain")),
        "kubernetes_version": info.get("kubeletVersion"), "container_runtime": info.get("containerRuntimeVersion"),
        "node_os": info.get("osImage"), "worker_nodes": len(workers), "concurrency_cap": int(quota or 0),
        "host": {"arch": platform.machine(), "system": platform.system(), "cpus": os.cpu_count()},
        "vm": {"cpus": vm.get("cpus"), "memory_bytes": vm.get("memory"), "disk_bytes": vm.get("disk")} if vm else None,
        "monitoring_stack_running": bool(sh("kubectl", "get", "ns", "monitoring", "-o", "name")),
        "dispatcher_replicas_at_start": sh("kubectl", "-n", "ksc-system", "get", "deploy", "ksc-dispatcher", "-o",
                                           "jsonpath={.status.readyReplicas}"),
        "workload": "python3.12: sum of squares over 1.5M integers (~0.2 s CPU), 500m CPU / 512Mi limits",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=200)
    ap.add_argument("--concurrency", type=int, nargs="+", default=[5, 20])
    ap.add_argument("--env", default=os.environ.get("BENCH_ENV", "kind-on-mac"))
    ap.add_argument("--out-dir", default="bench/results")
    ap.add_argument("--url", help="API URL; default: start a kubectl port-forward")
    a = ap.parse_args()

    key = os.environ.get("KSC_API_KEY") or sh("kubectl", "-n", "ksc-system", "exec", "deploy/ksc-api", "--",
                                              "python", "-m", "api.keys", "create", f"bench-{int(time.time())}")
    pf = None
    base = a.url
    if not base:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        pf = subprocess.Popen(["kubectl", "-n", "ksc-system", "port-forward", "svc/ksc-api", f"{port}:8080"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = f"http://127.0.0.1:{port}"
        for _ in range(40):
            try:
                if httpx.get(base + "/readyz", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.25)
    try:
        prov = provenance(a.env)
        levels = []
        for c in a.concurrency:
            print(f"== {a.runs} runs at concurrency {c}", flush=True)
            res = run_level(base, key, a.runs, c)
            print(json.dumps(res), flush=True)
            levels.append(res)
    finally:
        if pf:
            pf.terminate()
    out = {**prov, "levels": levels,
           "cold_start": {"measured": False,
                          "reason": "kind loads images into nodes; pulled-versus-pre-pulled needs a registry (EKS session)"}}
    path = Path(a.out_dir) / f"{prov['date']}-{a.env}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2) + "\n")
    print("wrote", path)
    return 0 if all(lv["infra_error_ratio"] == 0 for lv in levels) else 1


if __name__ == "__main__":
    sys.exit(main())
