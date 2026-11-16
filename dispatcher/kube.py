"""Thin Kubernetes wrapper. The rest of the dispatcher depends on this interface only, so unit
tests can swap in a fake."""
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

from dispatcher import podspec


@dataclass
class PodInfo:
    name: str
    run_id: str
    phase: str
    node: str | None
    started_at: datetime | None
    exit_code: int | None
    reason: str | None  # container terminated reason (e.g. OOMKilled) or pod-level reason


class Kube(Protocol):
    def ensure_run(self, run_id: str, job: dict[str, Any], code: str, stdin: str | None) -> None: ...
    def list_pods(self) -> dict[str, PodInfo]: ...
    def read_logs(self, pod_name: str, limit_bytes: int) -> str: ...
    def delete_run(self, run_id: str) -> None: ...


class RealKube:
    def __init__(self, namespace: str) -> None:
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        self.ns = namespace
        self.batch = client.BatchV1Api()
        self.core = client.CoreV1Api()

    def ensure_run(self, run_id: str, job: dict[str, Any], code: str, stdin: str | None) -> None:
        try:
            created = self.batch.create_namespaced_job(self.ns, job)
            uid = created.metadata.uid
        except ApiException as e:
            if e.status != 409:
                raise
            uid = self.batch.read_namespaced_job(podspec.job_name(run_id), self.ns).metadata.uid
        cm = podspec.build_configmap(run_id, code, stdin, self.ns, uid)
        try:
            self.core.create_namespaced_config_map(self.ns, cm)
        except ApiException as e:
            if e.status != 409:
                raise

    def list_pods(self) -> dict[str, PodInfo]:
        pods = self.core.list_namespaced_pod(self.ns, label_selector=f"{podspec.MANAGED_BY}=ksc")
        out: dict[str, PodInfo] = {}
        for p in pods.items:
            run_id = (p.metadata.labels or {}).get(podspec.RUN_ID_LABEL)
            if not run_id:
                continue
            exit_code = reason = started = None
            for cs in p.status.container_statuses or []:
                st = cs.state
                if st and st.terminated:
                    exit_code, reason = st.terminated.exit_code, st.terminated.reason
                    started = st.terminated.started_at
                elif st and st.running:
                    started = st.running.started_at
            out[run_id] = PodInfo(p.metadata.name, run_id, p.status.phase, p.spec.node_name,
                                  started, exit_code, reason or p.status.reason)
        return out

    def read_logs(self, pod_name: str, limit_bytes: int) -> str:
        return str(self.core.read_namespaced_pod_log(
            pod_name, self.ns, container="sandbox", limit_bytes=limit_bytes))

    def delete_run(self, run_id: str) -> None:
        name = podspec.job_name(run_id)
        for fn in (
            lambda: self.batch.delete_namespaced_job(name, self.ns, propagation_policy="Background"),
            lambda: self.core.delete_namespaced_config_map(name, self.ns),
        ):
            try:
                fn()
            except ApiException as e:
                if e.status != 404:
                    raise
