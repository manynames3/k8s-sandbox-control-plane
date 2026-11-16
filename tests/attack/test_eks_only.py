"""EKS-only attack: pods cannot reach the node's instance credentials.

Runs only in the AWS session (KSC_ENV=eks). It proves the node setting IMDSv2 required with hop limit 1
(infra/eks) rather than relying on the NetworkPolicy alone: the probe pod runs in a namespace that has NO
NetworkPolicy, so the only thing standing between it and the metadata service is the hop limit.
A hostNetwork pod on the same kind of node is the positive control: it must succeed, so a failure of the
pod-network probe cannot be a broken probe."""
import os
import subprocess
import time

import pytest

from tests.e2e.conftest import kubectl

pytestmark = [
    pytest.mark.attack,
    pytest.mark.skipif(os.environ.get("KSC_ENV") != "eks", reason="needs real EC2 nodes (the AWS session)"),
]

NS = "imds-probe"
TOKEN_CMD = ("curl -sS -m 5 -o /dev/null -w '%{http_code}' -X PUT http://169.254.169.254/latest/api/token "
             "-H 'X-aws-ec2-metadata-token-ttl-seconds: 60'")


def pod(name: str, host_network: bool) -> str:
    return f"""
apiVersion: v1
kind: Pod
metadata: {{name: {name}, namespace: {NS}}}
spec:
  restartPolicy: Never
  hostNetwork: {str(host_network).lower()}
  nodeSelector: {{ksc.dev/pool: sandbox}}
  tolerations: [{{key: ksc.dev/sandbox, operator: Equal, value: "true", effect: NoSchedule}}]
  containers:
    - name: c
      image: curlimages/curl:8.10.1
      command: ["sh", "-c", "{TOKEN_CMD}"]
"""


def run_probe(name: str, host_network: bool) -> tuple[str, str]:
    subprocess.run(["kubectl", "apply", "-f", "-"], input=pod(name, host_network), text=True, check=True,
                   capture_output=True)
    deadline = time.time() + 120
    phase = ""
    while time.time() < deadline:
        phase = kubectl("-n", NS, "get", "pod", name, "-o", "jsonpath={.status.phase}", check=False)
        if phase in ("Succeeded", "Failed"):
            break
        time.sleep(2)
    return phase, kubectl("-n", NS, "logs", name, check=False)


def test_pod_network_cannot_reach_instance_metadata_but_host_network_can():
    kubectl("create", "namespace", NS, check=False)
    try:
        host_phase, host_out = run_probe("imds-host", host_network=True)
        pod_phase, pod_out = run_probe("imds-pod", host_network=False)
    finally:
        kubectl("delete", "namespace", NS, "--wait=false", check=False)
    assert host_phase == "Succeeded" and host_out.strip() == "200", (host_phase, host_out)  # control works
    assert pod_phase == "Failed", (pod_phase, pod_out)  # curl exits non-zero: no response within 5 s
    assert "200" not in pod_out
