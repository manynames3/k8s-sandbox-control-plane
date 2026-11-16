"""Dispatcher core: claim queued runs, observe pods, record results, reconcile, sweep.

Database holds intent, the cluster holds reality, the dispatcher converges them (ADR 6).
Cleanup layers, innermost first: (1) the wrapper timeout, (2) Job activeDeadlineSeconds,
(3) reconcile (cluster object without a live run, or a live run without a cluster object),
(4) the sweeper (age-based, ignores the database except for the run's timeout)."""
import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from api import states
from api.models import Run, RunEvent
from dispatcher import logparse, metrics, podspec
from dispatcher.kube import Kube, PodInfo

log = logging.getLogger("ksc.dispatcher")
CLAIM_LOCK_KEY = 0x6B7363_0001
SWEEP_LOCK_KEY = 0x6B7363_0002
MAX_LOG_READ = 4 * 1024 * 1024  # raw bytes read from the log API (framing overhead included)
MAX_STREAM = 1024 * 1024
IMAGE_ERRORS = frozenset({"ErrImagePull", "ImagePullBackOff", "InvalidImageName",
                          "CreateContainerConfigError", "CreateContainerError"})


@dataclass(frozen=True)
class Timing:
    sweep_max_age: float = 600       # anything older than this is swept
    sweep_grace: float = 300         # a run's objects are swept after timeout + grace
    sweep_interval: float = 30
    requeue_grace: float = 10        # a freshly claimed run gets this long to get its Job
    image_error_after: float = 30    # image pull problems persisting this long -> infra_error
    schedule_after: float = 120      # Pending this long -> infra_error


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
        metrics.k8s_api_errors["ensure_run"] += 1
        log.warning("dispatch failed, re-queueing", extra={"run_id": c.id, "error": str(e)})
        # Names are deterministic, so a later retry is idempotent even if the create half-succeeded.
        _transition(engine, c.id, states.QUEUED)


def classify(pod: PodInfo, parsed: logparse.ParsedLogs) -> tuple[str, str | None]:
    if pod.reason == "OOMKilled":
        return "oom_killed", "OOMKilled"
    if pod.deleting and pod.exit_code != 0:
        return "infra_error", "pod_deleted"  # killed from outside; not the user's code failing
    if pod.reason == "DeadlineExceeded" and pod.exit_code is None:
        return "timed_out", "deadline_exceeded"  # layer 2 fired: the wrapper did not
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
        metrics.k8s_api_errors["read_logs"] += 1
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


def fail_run(engine: Engine, kube: Kube, run_id: str, status: str, reason: str) -> None:
    """Terminal state decided by the platform (no pod result to read)."""
    if _transition(engine, run_id, status, termination_reason=reason, finished_at=_now()):
        log.info("run finished", extra={"run_id": run_id, "status": status, "error": reason})
    kube.delete_run(run_id)


def observe(engine: Engine, kube: Kube, timing: Timing, now: datetime | None = None) -> None:
    now = now or _now()
    with Session(engine) as s:
        live = {str(r.id): (r.status, r.dispatched_at) for r in s.scalars(
            select(Run).where(Run.status.in_([states.DISPATCHING, states.RUNNING])))}
    if not live:
        return
    pods = kube.list_pods()
    jobs = None
    for run_id, (status, dispatched_at) in live.items():
        pod = pods.get(run_id)
        age = (now - dispatched_at).total_seconds() if dispatched_at else 0.0
        if pod is None:
            if jobs is None:
                jobs = {o.run_id: o for o in kube.list_objects() if o.kind == "jobs"}
            job = jobs.get(run_id)
            if job and job.failed_reason == "DeadlineExceeded":
                fail_run(engine, kube, run_id, "timed_out", "deadline_exceeded")
            continue  # otherwise reconcile decides
        if pod.phase in ("Succeeded", "Failed"):
            finalize(engine, kube, run_id, pod)
        elif pod.phase == "Running":
            if status == states.DISPATCHING:
                _transition(engine, run_id, states.RUNNING, node=pod.node, started_at=pod.started_at)
        elif pod.phase == "Pending":
            if pod.waiting_reason in IMAGE_ERRORS and age > timing.image_error_after:
                fail_run(engine, kube, run_id, "infra_error", "image_error")
            elif age > timing.schedule_after:
                fail_run(engine, kube, run_id, "infra_error", "schedule_timeout")


def _uuid_or_none(v: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(v) if v else None
    except ValueError:
        return None


def reconcile(engine: Engine, kube: Kube, timing: Timing, now: datetime | None = None) -> None:
    """Converge database and cluster. Runs every tick (cheap at this scale) and at startup."""
    now = now or _now()
    objs = kube.list_objects()
    by_run: dict[uuid.UUID, list] = defaultdict(list)  # type: ignore[type-arg]
    for o in objs:
        rid = _uuid_or_none(o.run_id)
        if rid:
            by_run[rid].append(o)  # objects without a run id are the sweeper's business
    with Session(engine) as s:
        live = {r.id: r for r in s.scalars(
            select(Run).where(Run.status.in_([states.DISPATCHING, states.RUNNING])))}
        known = {r.id: r.status for r in s.scalars(select(Run).where(Run.id.in_(list(by_run))))} \
            if by_run else {}
    # 1. live run without a Job
    for rid, run in live.items():
        if any(o.kind == "jobs" for o in by_run.get(rid, [])):
            continue
        if run.dispatched_at and (now - run.dispatched_at).total_seconds() < timing.requeue_grace:
            continue
        if run.status == states.DISPATCHING and run.started_at is None:
            if _transition(engine, str(rid), states.QUEUED):
                metrics.reconcile_corrections["requeue_no_job"] += 1
                log.warning("run had no job, re-queued", extra={"run_id": str(rid)})
        else:
            fail_run(engine, kube, str(rid), "infra_error", "job_lost")
            metrics.reconcile_corrections["job_lost"] += 1
    # 2. cluster objects whose run is gone or already terminal
    for rid, items in by_run.items():
        status = known.get(rid)
        if status is not None and not states.is_terminal(status):
            continue
        reason = "no_run" if status is None else ("cancelled" if status == "cancelled" else "terminal_leftover")
        kube.delete_run(str(rid))
        for o in items:
            if o.kind == "pods":
                kube.delete_object("pods", o.name)
        metrics.reconcile_corrections[reason] += 1


def sweep(engine: Engine, kube: Kube, timing: Timing, now: datetime | None = None) -> int:
    """Last layer: delete managed objects older than timeout + grace (and anything older than the
    max age). Independent of run state. One replica at a time via a transaction-level lock."""
    now = now or _now()
    deleted = 0
    with Session(engine) as s, s.begin():
        if not s.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": SWEEP_LOCK_KEY}).scalar():
            return 0
        objs = kube.list_objects()
        ids = [r for r in (_uuid_or_none(o.run_id) for o in objs) if r]
        timeouts = {r.id: r.timeout_seconds for r in s.scalars(select(Run).where(Run.id.in_(ids)))} \
            if ids else {}
        for o in objs:
            rid = _uuid_or_none(o.run_id)
            limit = timing.sweep_max_age
            if rid in timeouts:
                limit = min(limit, timeouts[rid] + timing.sweep_grace)
            if now - o.created > timedelta(seconds=limit):
                kube.delete_object(o.kind, o.name)
                metrics.sweeper_deletions[o.kind] += 1
                deleted += 1
                log.warning("sweeper deleted stale object",
                            extra={"run_id": o.run_id or "", "error": f"{o.kind}/{o.name}"})
    return deleted


def tick(engine: Engine, kube: Kube, cap: int, sandbox: podspec.SandboxSettings,
         timing: Timing = Timing()) -> None:
    for c in claim(engine, cap):
        dispatch(engine, kube, c, sandbox)
    observe(engine, kube, timing)
    reconcile(engine, kube, timing)
