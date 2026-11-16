"""Dispatcher core: claim queued runs, observe pods, record results, clean up.

Database holds intent, the cluster holds reality, the dispatcher converges them (ADR 6)."""
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from api import states
from api.models import Run, RunEvent
from dispatcher import logparse, podspec
from dispatcher.kube import Kube, PodInfo

log = logging.getLogger("ksc.dispatcher")
CLAIM_LOCK_KEY = 0x6B7363_0001  # transaction-level advisory lock serialises the claim step
MAX_LOG_READ = 4 * 1024 * 1024  # raw bytes read from the log API (framing overhead included)
MAX_STREAM = 1024 * 1024


@dataclass(frozen=True)
class Claimed:
    id: str
    language: str
    code: str
    stdin: str | None
    timeout_seconds: int


def _now() -> datetime:
    return datetime.now(UTC)


def claim(engine: Engine, cap: int) -> list[Claimed]:
    """Claim up to (cap - active) queued runs. Safe with several replicas: the xact-level advisory
    lock serialises the count-then-claim step, SKIP LOCKED keeps row claims disjoint."""
    with Session(engine) as s, s.begin():
        if not s.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": CLAIM_LOCK_KEY}).scalar():
            return []
        active = s.execute(
            text("SELECT count(*) FROM runs WHERE status IN ('dispatching','running')")).scalar_one()
        room = cap - active
        if room <= 0:
            return []
        rows = s.scalars(
            select(Run).where(Run.status == states.QUEUED).order_by(Run.created_at)
            .limit(room).with_for_update(skip_locked=True)
        ).all()
        out = []
        for r in rows:
            r.status = states.DISPATCHING
            r.dispatched_at = _now()
            s.add(RunEvent(run_id=r.id, kind="state", data={"status": states.DISPATCHING}))
            out.append(Claimed(str(r.id), r.language, r.code, r.stdin, r.timeout_seconds))
        return out


def _transition(engine: Engine, run_id: str, new: str, **fields: object) -> bool:
    """Guarded transition: applies only if the state machine allows it from the *current* row."""
    with Session(engine) as s, s.begin():
        run = s.scalar(select(Run).where(Run.id == uuid.UUID(run_id)).with_for_update())
        if run is None or not states.can_transition(run.status, new):
            return False
        s.execute(update(Run).where(Run.id == run.id).values(status=new, **fields))
        s.add(RunEvent(run_id=run.id, kind="state", data={"status": new}))
        return True


def dispatch(engine: Engine, kube: Kube, c: Claimed, sandbox: podspec.SandboxSettings) -> None:
    job = podspec.build_job(c.id, c.language, c.timeout_seconds, sandbox)
    try:
        kube.ensure_run(c.id, job, c.code, c.stdin)
    except Exception as e:  # noqa: BLE001 - any failure to create must not strand the run
        log.warning("dispatch failed, re-queueing", extra={"run_id": c.id, "error": str(e)})
        # Names are deterministic, so a later retry is idempotent even if the create half-succeeded.
        _transition(engine, c.id, states.QUEUED)


def classify(pod: PodInfo, parsed: logparse.ParsedLogs) -> tuple[str, str | None]:
    if pod.reason == "OOMKilled":
        return "oom_killed", "OOMKilled"
    if pod.exit_code is None:
        return "infra_error", pod.reason or "no_container_status"
    if pod.exit_code == 124 or parsed.timeout:
        return "timed_out", "timeout"
    if pod.exit_code == 0:
        return "succeeded", None
    return "failed", "nonzero_exit"


def finalize(engine: Engine, kube: Kube, run_id: str, pod: PodInfo) -> None:
    raw = ""
    try:
        raw = kube.read_logs(pod.name, MAX_LOG_READ)
    except Exception as e:  # noqa: BLE001
        log.warning("log read failed", extra={"run_id": run_id, "error": str(e)})
    parsed = logparse.parse(raw, MAX_STREAM)
    status, reason = classify(pod, parsed)
    ok = _transition(
        engine, run_id, status, exit_code=pod.exit_code, termination_reason=reason,
        stdout=parsed.stdout, stderr=parsed.stderr, truncated=parsed.truncated,
        node=pod.node, started_at=pod.started_at, finished_at=_now(),
    )
    if ok:
        with Session(engine) as s, s.begin():
            for kind, data in (("stdout", parsed.stdout), ("stderr", parsed.stderr)):
                if data:
                    s.add(RunEvent(run_id=uuid.UUID(run_id), kind=kind, data={"text": data}))
        log.info("run finished", extra={"run_id": run_id, "status": status})
    # Result is persisted first; only then are the cluster objects removed.
    kube.delete_run(run_id)


def observe(engine: Engine, kube: Kube) -> None:
    with Session(engine) as s:
        live = {str(r.id): r.status for r in s.scalars(
            select(Run).where(Run.status.in_([states.DISPATCHING, states.RUNNING])))}
    if not live:
        return
    pods = kube.list_pods()
    for run_id, status in live.items():
        pod = pods.get(run_id)
        if pod is None:
            continue  # reconcile handles runs without cluster objects (phase 3)
        if pod.phase in ("Succeeded", "Failed"):
            finalize(engine, kube, run_id, pod)
        elif pod.phase == "Running" and status == states.DISPATCHING:
            _transition(engine, run_id, states.RUNNING, node=pod.node, started_at=pod.started_at)


def tick(engine: Engine, kube: Kube, cap: int, sandbox: podspec.SandboxSettings) -> None:
    for c in claim(engine, cap):
        dispatch(engine, kube, c, sandbox)
    observe(engine, kube)
