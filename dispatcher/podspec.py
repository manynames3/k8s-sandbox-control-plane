"""Builds every sandbox Job from one fixed template. User input never reaches the spec
except as ConfigMap data, the timeout number, and the run id (validated UUID)."""
from dataclasses import dataclass, field
from typing import Any

SANDBOX_UID = 10001
MANAGED_BY = "app.kubernetes.io/managed-by"
RUN_ID_LABEL = "ksc.dev/run-id"
DEADLINE_GRACE = 30
CODE_FILES = {"python3.12": "main.py", "node20": "main.js"}  # key inside the ConfigMap, per language


@dataclass(frozen=True)
class SandboxSettings:
    namespace: str = "sandboxes"
    images: dict[str, str] = field(default_factory=lambda: {
        "python3.12": "ksc/runner-python:dev", "node20": "ksc/runner-node:dev"})
    cpu: str = "500m"
    memory: str = "512Mi"
    ephemeral_storage: str = "128Mi"
    tmp_size_limit: str = "64Mi"
    node_selector: dict[str, str] = field(default_factory=dict)
    tolerations: list[dict[str, Any]] = field(default_factory=list)
    runtime_class: str = ""  # e.g. gvisor; empty means the node's default runtime (runc)


def job_name(run_id: str) -> str:
    return f"run-{run_id}"


def labels(run_id: str) -> dict[str, str]:
    return {MANAGED_BY: "ksc", RUN_ID_LABEL: run_id}


def build_job(run_id: str, language: str, timeout_seconds: int, s: SandboxSettings) -> dict[str, Any]:
    name = job_name(run_id)
    pod_spec: dict[str, Any] = {
        "restartPolicy": "Never",
        "serviceAccountName": "sandbox",
        "automountServiceAccountToken": False,
        "enableServiceLinks": False,
        "hostNetwork": False,
        "hostPID": False,
        "hostIPC": False,
        "terminationGracePeriodSeconds": 5,
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": SANDBOX_UID,
            "runAsGroup": SANDBOX_UID,
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "topologySpreadConstraints": [
            {
                "maxSkew": 1,
                "topologyKey": "kubernetes.io/hostname",
                "whenUnsatisfiable": "ScheduleAnyway",
                "labelSelector": {"matchLabels": {MANAGED_BY: "ksc"}},
            }
        ],
        "containers": [
            {
                "name": "sandbox",
                "image": s.images[language],
                "imagePullPolicy": "IfNotPresent",
                "env": [{"name": "KSC_TIMEOUT_SECONDS", "value": str(timeout_seconds)}],
                "securityContext": {
                    "allowPrivilegeEscalation": False,
                    "readOnlyRootFilesystem": True,
                    "runAsNonRoot": True,
                    "capabilities": {"drop": ["ALL"]},
                    "seccompProfile": {"type": "RuntimeDefault"},
                },
                "resources": {
                    "requests": {"cpu": s.cpu, "memory": s.memory, "ephemeral-storage": s.ephemeral_storage},
                    "limits": {"cpu": s.cpu, "memory": s.memory, "ephemeral-storage": s.ephemeral_storage},
                },
                "volumeMounts": [
                    {"name": "tmp", "mountPath": "/tmp"},
                    {"name": "code", "mountPath": "/code", "readOnly": True},
                ],
            }
        ],
        "volumes": [
            {"name": "tmp", "emptyDir": {"sizeLimit": s.tmp_size_limit}},
            {"name": "code", "configMap": {"name": name, "defaultMode": 0o444}},
        ],
    }
    if s.node_selector:
        pod_spec["nodeSelector"] = dict(s.node_selector)
    if s.tolerations:
        pod_spec["tolerations"] = list(s.tolerations)
    if s.runtime_class:
        pod_spec["runtimeClassName"] = s.runtime_class
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "namespace": s.namespace, "labels": labels(run_id)},
        "spec": {
            "backoffLimit": 0,
            "ttlSecondsAfterFinished": 3600,  # the dispatcher deletes it long before; this is only a backstop
            "activeDeadlineSeconds": timeout_seconds + DEADLINE_GRACE,
            "template": {"metadata": {"labels": labels(run_id)}, "spec": pod_spec},
        },
    }


def build_configmap(run_id: str, code: str, stdin: str | None, namespace: str,
                    job_uid: str, language: str = "python3.12") -> dict[str, Any]:
    data = {CODE_FILES[language]: code}
    if stdin is not None:
        data["stdin"] = stdin
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": job_name(run_id),
            "namespace": namespace,
            "labels": labels(run_id),
            "ownerReferences": [
                {"apiVersion": "batch/v1", "kind": "Job", "name": job_name(run_id), "uid": job_uid}
            ],
        },
        "immutable": True,
        "data": data,
    }
