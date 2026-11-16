#!/usr/bin/env python3
"""Records a curated set of real runs against the local cluster and exports web/public/snapshot.json
for the public replay site (`make snapshot`).

The set: one run per terminal state, every attack from web/src/attacks.json, the latest benchmark.
Needs: the cluster up (make up). It port-forwards the API itself and never exposes anything."""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
import snapshot_lib as lib

ROOT = Path(__file__).resolve().parents[1]
TERMINAL = {"succeeded", "failed", "timed_out", "oom_killed", "cancelled", "infra_error"}
SLEEPER = "import time\nwhile True:\n    time.sleep(1)\n"


def kubectl(*args: str) -> str:
    return subprocess.run(["kubectl", *args], capture_output=True, text=True, check=False).stdout.strip()


def submit(c: httpx.Client, language: str, code: str, timeout: int) -> str:
    r = c.post("/v1/runs", json={"language": language, "code": code, "timeout_seconds": timeout})
    r.raise_for_status()
    return str(r.json()["id"])


def wait(c: httpx.Client, rid: str, status: set[str], limit: float = 150) -> dict:
    deadline = time.time() + limit
    while time.time() < deadline:
        run = c.get(f"/v1/runs/{rid}").json()
        if run["status"] in status:
            return run  # type: ignore[no-any-return]
        time.sleep(0.3)
    raise TimeoutError(f"{rid} never reached {status}")


def events(c: httpx.Client, rid: str) -> list[dict]:
    out, kind = [], None
    with c.stream("GET", f"/v1/runs/{rid}/events", timeout=200) as r:
        for line in r.iter_lines():
            if line.startswith("event: "):
                kind = line[7:]
            elif line.startswith("data: ") and kind:
                out.append({"event": kind, "data": json.loads(line[6:])})
    return out


def record(c: httpx.Client) -> list[tuple[str, str, str | None, str]]:
    """Returns (label, kind, attack_id, run_id) for every recorded run."""
    attacks = json.loads((ROOT / "web/src/attacks.json").read_text())
    rec: list[tuple[str, str, str | None, str]] = []

    def run_and_wait(label: str, kind: str, attack_id: str | None, language: str, code: str, timeout: int) -> None:
        print(f"recording: {label}", flush=True)
        rid = submit(c, language, code, timeout)
        wait(c, rid, TERMINAL)
        rec.append((label, kind, attack_id, rid))

    run_and_wait("Hello, Python", "state", None, "python3.12", attacks["hello"]["python3.12"], 30)
    run_and_wait("Hello, Node", "state", None, "node20", attacks["hello"]["node20"], 30)
    run_and_wait("Exit code 3 (failed)", "state", None, "python3.12",
                 "import sys\nprint('about to fail')\nprint('error: bad input', file=sys.stderr)\nsys.exit(3)\n", 30)

    for a in attacks["attacks"]:
        run_and_wait(a["label"], "attack", a["id"], a["language"], a["code"], a["timeout"])

    print("recording: cancelled run", flush=True)
    rid = submit(c, "python3.12", SLEEPER, 120)
    wait(c, rid, {"running"})
    time.sleep(2)
    c.delete(f"/v1/runs/{rid}")
    wait(c, rid, TERMINAL)
    rec.append(("Cancelled while running", "state", None, rid))

    print("recording: infra error (job deleted mid-run)", flush=True)
    rid = submit(c, "python3.12", SLEEPER, 120)
    wait(c, rid, {"running"})
    time.sleep(2)
    kubectl("-n", "sandboxes", "delete", "job", f"run-{rid}", "--wait=false")
    wait(c, rid, TERMINAL, 90)
    rec.append(("Platform fault (infra_error)", "state", None, rid))
    return rec


def main() -> int:
    key = os.environ.get("KSC_API_KEY") or kubectl("-n", "ksc-system", "exec", "deploy/ksc-api", "--", "python",
                                                   "-m", "api.keys", "create", f"showcase-{int(time.time())}")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    pf = subprocess.Popen(["kubectl", "-n", "ksc-system", "port-forward", "svc/ksc-api", f"{port}:8080"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        c = httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {key}"}, timeout=15)
        for _ in range(40):
            try:
                if c.get("/readyz").status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.25)
        recorded = record(c)
        nodes = lib.NodeAliases()
        runs = [lib.scrub_run(c.get(f"/v1/runs/{rid}").json(), events(c, rid), label, kind, attack_id, nodes)
                for label, kind, attack_id, rid in recorded]
    finally:
        pf.terminate()

    results = sorted((ROOT / "bench/results").glob("*.json"))
    bench = None
    meta = {"date": time.strftime("%Y-%m-%d"), "environment": "kind-on-mac"}
    if results:
        data = json.loads(results[-1].read_text())
        bench = lib.summarize_benchmark(data, results[-1].relative_to(ROOT).as_posix())
        meta = {**meta, **{k: data[k] for k in ("kubernetes_version", "container_runtime", "worker_nodes",
                                                "concurrency_cap", "git_sha") if k in data}}
    repo = os.environ.get("GITHUB_REPOSITORY", "manynames3/k8s-sandbox-control-plane")
    links = {"repo": f"https://github.com/{repo}", "ci": f"https://github.com/{repo}/actions/workflows/ci.yml",
             "results": f"https://github.com/{repo}/blob/main/{bench['results_file']}" if bench else ""}
    snap = lib.build_snapshot(runs, bench, meta, links)
    lib.assert_clean(snap, forbidden=[os.environ.get("USER", ""), "Aiden", "aiden"])
    out = ROOT / "web/public/snapshot.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snap, indent=1) + "\n")
    print(f"wrote {out} ({out.stat().st_size // 1024} kB, {len(runs)} runs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
