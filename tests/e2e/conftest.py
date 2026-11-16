"""e2e fixtures: talk to the real API on the kind cluster through kubectl port-forward."""
import socket
import subprocess
import time

import httpx
import pytest

NS_SYS, NS_SBX = "ksc-system", "sandboxes"


def kubectl(*args: str, check: bool = True) -> str:
    r = subprocess.run(["kubectl", *args], capture_output=True, text=True, check=check)
    return r.stdout.strip()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="session")
def api_url():
    port = _free_port()
    pf = subprocess.Popen(
        ["kubectl", "-n", NS_SYS, "port-forward", "svc/ksc-api", f"{port}:8080"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(40):
            try:
                if httpx.get(url + "/readyz", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.25)
        else:
            pytest.fail("API did not become ready via port-forward")
        yield url
    finally:
        pf.terminate()


@pytest.fixture(scope="session")
def client(api_url):
    key = kubectl("-n", NS_SYS, "exec", "deploy/ksc-api", "--", "python", "-m", "api.keys",
                  "create", f"e2e-{int(time.time())}")
    with httpx.Client(base_url=api_url, headers={"Authorization": f"Bearer {key}"}, timeout=10) as c:
        yield c


TERMINAL = {"succeeded", "failed", "timed_out", "oom_killed", "cancelled", "infra_error"}


def wait_terminal(client: httpx.Client, run_id: str, timeout: float = 90) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = client.get(f"/v1/runs/{run_id}").json()
        if run["status"] in TERMINAL:
            return run
        time.sleep(0.5)
    pytest.fail(f"run {run_id} not terminal after {timeout}s: {run['status']}")


def assert_cleaned_up(run_id: str, timeout: float = 30) -> None:
    """Cleanup proof: Job, Pod and ConfigMap for the run are gone within a bounded wait."""
    deadline = time.time() + timeout
    sel = f"ksc.dev/run-id={run_id}"
    while time.time() < deadline:
        left = [kubectl("-n", NS_SBX, "get", kind, "-l", sel, "-o", "name", check=False)
                for kind in ("jobs", "pods", "configmaps")]
        if not any(left):
            return
        time.sleep(0.5)
    pytest.fail(f"resources left for run {run_id}: {left}")


def wait_status(client: httpx.Client, run_id: str, status: str, timeout: float = 60) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = client.get(f"/v1/runs/{run_id}").json()
        if run["status"] == status:
            return run
        if run["status"] in TERMINAL:
            pytest.fail(f"run {run_id} reached {run['status']} while waiting for {status}")
        time.sleep(0.3)
    pytest.fail(f"run {run_id} never reached {status}")
