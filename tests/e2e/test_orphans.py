"""Orphan proof: a managed Job created by hand (no run behind it) is removed by the sweeper.

The sweeper normally waits 10 minutes, so this test temporarily shortens it on the dispatcher and
restores the setting afterwards. It must not run concurrently with other e2e tests."""
import time

import pytest

from tests.e2e.conftest import NS_SBX, NS_SYS, kubectl

pytestmark = pytest.mark.e2e

ORPHAN = """
apiVersion: batch/v1
kind: Job
metadata:
  name: orphan-by-hand
  namespace: sandboxes
  labels: {app.kubernetes.io/managed-by: ksc}
spec:
  backoffLimit: 0
  template:
    metadata:
      labels: {app.kubernetes.io/managed-by: ksc}
    spec:
      restartPolicy: Never
      serviceAccountName: sandbox
      automountServiceAccountToken: false
      securityContext: {runAsNonRoot: true, runAsUser: 10001, seccompProfile: {type: RuntimeDefault}}
      containers:
        - name: c
          image: busybox:1.36
          command: ["sleep", "3600"]
          securityContext: {allowPrivilegeEscalation: false, capabilities: {drop: [ALL]}}
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: orphan-cm-by-hand
  namespace: sandboxes
  labels: {app.kubernetes.io/managed-by: ksc}
data: {x: "1"}
"""


@pytest.fixture
def fast_sweeper():
    kubectl("-n", NS_SYS, "set", "env", "deploy/ksc-dispatcher", "SWEEP_MAX_AGE_SECONDS=15",
            "SWEEP_GRACE_SECONDS=0", "SWEEP_INTERVAL_SECONDS=3")
    kubectl("-n", NS_SYS, "rollout", "status", "deploy/ksc-dispatcher", "--timeout=120s")
    try:
        yield
    finally:
        kubectl("-n", NS_SYS, "set", "env", "deploy/ksc-dispatcher", "SWEEP_MAX_AGE_SECONDS-",
                "SWEEP_GRACE_SECONDS-", "SWEEP_INTERVAL_SECONDS-")
        kubectl("-n", NS_SYS, "rollout", "status", "deploy/ksc-dispatcher", "--timeout=120s")


def test_sweeper_removes_hand_made_orphans(fast_sweeper):
    import subprocess
    subprocess.run(["kubectl", "apply", "-f", "-"], input=ORPHAN, text=True, check=True,
                   capture_output=True)
    assert "orphan-by-hand" in kubectl("-n", NS_SBX, "get", "job", "-o", "name")
    deadline = time.time() + 90
    while time.time() < deadline:
        left = kubectl("-n", NS_SBX, "get", "jobs,pods,configmaps", "-l",
                       "app.kubernetes.io/managed-by=ksc", "-o", "name", check=False)
        if not left:
            break
        time.sleep(2)
    else:
        pytest.fail(f"sweeper did not remove orphans: {left}")
    logs = kubectl("-n", NS_SYS, "logs", "deploy/ksc-dispatcher", "--tail=200", check=False)
    assert "sweeper deleted stale object" in logs
