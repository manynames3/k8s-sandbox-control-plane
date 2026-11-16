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
    waiting_reason: str | None = None  # e.g. ErrImagePull while Pending
    created: datetime | None = None
    deleting: bool = False  # deletionTimestamp set: something outside the run removed the pod


@dataclass
class Obj:
    """A managed object (job, pod or configmap) as seen by reconcile and the sweeper."""
    kind: str  # jobs | pods | configmaps
    name: str
    run_id: str | None
    created: datetime
    failed_reason: str | None = None  # Job condition Failed reason, e.g. DeadlineExceeded


class Kube(Protocol):
    def ensure_run(self, run_id: str, job: dict[str, Any], code: str, stdin: str | None) -> None: ...
    def list_pods(self) -> dict[str, PodInfo]: ...
    def list_objects(self) -> list[Obj]: ...
    def read_logs(self, pod_name: str, limit_bytes: int) -> str: ...
    def delete_run(self, run_id: str) -> None: ...
    def delete_object(self, kind: str, name: str) -> None: ...


def _ignore_404(fn: Any) -> None:
    try:
        fn()
    except ApiException as e:
        if e.status != 404:
            raise


class RealKube:
    def __init__(self, namespace: str) -> None:
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        self.ns = namespace
        self.batch = client.BatchV1Api()
        self.core = client.CoreV1Api()
        self.selector = f"{podspec.MANAGED_BY}=ksc"

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
        pods = self.core.list_namespaced_pod(self.ns, label_selector=self.selector)
        out: dict[str, PodInfo] = {}
        for p in pods.items:
            run_id = (p.metadata.labels or {}).get(podspec.RUN_ID_LABEL)
            if not run_id:
                continue
            exit_code = reason = started = waiting = None
            for cs in p.status.container_statuses or []:
                st = cs.state
                if st and st.terminated:
                    exit_code, reason = st.terminated.exit_code, st.terminated.reason
                    started = st.terminated.started_at
                elif st and st.running:
                    started = st.running.started_at
                elif st and st.waiting:
                    waiting = st.waiting.reason
            out[run_id] = PodInfo(p.metadata.name, run_id, p.status.phase, p.spec.node_name,
                                  started, exit_code, reason or p.status.reason, waiting,
                                  p.metadata.creation_timestamp,
                                  p.metadata.deletion_timestamp is not None)
        return out

    def list_objects(self) -> list[Obj]:
        out: list[Obj] = []
        for j in self.batch.list_namespaced_job(self.ns, label_selector=self.selector).items:
            failed = next((c.reason for c in (j.status.conditions or [])
                           if c.type == "Failed" and c.status == "True"), None)
            out.append(Obj("jobs", j.metadata.name, (j.metadata.labels or {}).get(podspec.RUN_ID_LABEL),
                           j.metadata.creation_timestamp, failed))
        for p in self.core.list_namespaced_pod(self.ns, label_selector=self.selector).items:
            out.append(Obj("pods", p.metadata.name, (p.metadata.labels or {}).get(podspec.RUN_ID_LABEL),
                           p.metadata.creation_timestamp))
        for c in self.core.list_namespaced_config_map(self.ns, label_selector=self.selector).items:
            out.append(Obj("configmaps", c.metadata.name,
                           (c.metadata.labels or {}).get(podspec.RUN_ID_LABEL), c.metadata.creation_timestamp))
        return out

    def read_logs(self, pod_name: str, limit_bytes: int) -> str:
        return str(self.core.read_namespaced_pod_log(
            pod_name, self.ns, container="sandbox", limit_bytes=limit_bytes))

    def delete_object(self, kind: str, name: str) -> None:
        if kind == "jobs":
            _ignore_404(lambda: self.batch.delete_namespaced_job(
                name, self.ns, propagation_policy="Background"))
        elif kind == "pods":
            _ignore_404(lambda: self.core.delete_namespaced_pod(name, self.ns, grace_period_seconds=0))
        elif kind == "configmaps":
            _ignore_404(lambda: self.core.delete_namespaced_config_map(name, self.ns))
        else:
            raise ValueError(kind)

    def delete_run(self, run_id: str) -> None:
        name = podspec.job_name(run_id)
        self.delete_object("jobs", name)
        self.delete_object("configmaps", name)
