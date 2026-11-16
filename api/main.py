import json
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from api import states
from api.auth import require_key
from api.db import get_engine
from api.models import Run, RunEvent
from api.schemas import MAX_CODE_BYTES, SUPPORTED, BatchCreate, BatchOut, RunCreate, RunOut

app = FastAPI(title="k8s-sandbox-control-plane", version="0.1.0")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def readyz(response: Response) -> dict[str, str]:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError, RuntimeError):
        response.status_code = 503
        return {"status": "database_unavailable"}
    return {"status": "ready"}


def validate(body: RunCreate) -> None:
    """Input validation only: language allowlist, size cap, timeout range (ADR 5)."""
    if body.language not in SUPPORTED:
        raise HTTPException(422, f"language {body.language} is not enabled")
    if len(body.code.encode()) > MAX_CODE_BYTES:
        raise HTTPException(422, f"code exceeds {MAX_CODE_BYTES} bytes")


def insert_run(s: Session, key_id: uuid.UUID, body: RunCreate, idem: str | None) -> Run:
    """Insert one run (its own queue entry) or return the existing one for a repeated key."""
    validate(body)
    run_id = uuid.uuid4()
    stmt = (
        insert(Run)
        .values(id=run_id, api_key_id=key_id, idempotency_key=idem, language=body.language,
                code=body.code, stdin=body.stdin, timeout_seconds=body.timeout_seconds)
        .on_conflict_do_nothing(constraint="uq_runs_idempotency")
        .returning(Run.id)
    )
    if s.execute(stmt).scalar_one_or_none() is None:  # same Idempotency-Key seen before
        existing = s.scalar(select(Run).where(Run.api_key_id == key_id, Run.idempotency_key == idem))
        assert existing is not None
        same = (existing.language, existing.code, existing.stdin, existing.timeout_seconds) == (
            body.language, body.code, body.stdin, body.timeout_seconds)
        if not same:
            raise HTTPException(409, "Idempotency-Key reused with a different request")
        return existing
    s.add(RunEvent(run_id=run_id, kind="state", data={"status": "queued"}))
    s.flush()
    return s.get_one(Run, run_id)


@app.post("/v1/runs", status_code=202, response_model=RunOut)
def create_run(
    body: RunCreate,
    key_id: uuid.UUID = Depends(require_key),
    idempotency_key: str | None = Header(default=None),
) -> Run:
    with Session(get_engine(), expire_on_commit=False) as s, s.begin():
        return insert_run(s, key_id, body, idempotency_key)


@app.post("/v1/runs:batch", status_code=202, response_model=BatchOut)
def create_batch(
    body: BatchCreate,
    key_id: uuid.UUID = Depends(require_key),
    idempotency_key: str | None = Header(default=None),
) -> BatchOut:
    """Up to 20 runs in one transaction: all are queued or none. With an Idempotency-Key, run i
    uses the key '<key>:<i>', so retrying the whole batch returns the same runs."""
    with Session(get_engine(), expire_on_commit=False) as s, s.begin():
        runs = [insert_run(s, key_id, r, f"{idempotency_key}:{i}" if idempotency_key else None)
                for i, r in enumerate(body.runs)]
        return BatchOut(runs=[RunOut.model_validate(r) for r in runs])


@app.get("/v1/runs/{run_id}", response_model=RunOut)
def get_run(run_id: uuid.UUID, key_id: uuid.UUID = Depends(require_key)) -> Run:
    with Session(get_engine(), expire_on_commit=False) as s:
        run = s.get(Run, run_id)
        if run is None or run.api_key_id != key_id:
            raise HTTPException(404, "run not found")
        return run


@app.delete("/v1/runs/{run_id}", response_model=RunOut)
def cancel_run(run_id: uuid.UUID, key_id: uuid.UUID = Depends(require_key)) -> Run:
    """Marks the run cancelled. The dispatcher removes its cluster objects on its next tick."""
    with Session(get_engine(), expire_on_commit=False) as s, s.begin():
        run = s.scalar(select(Run).where(Run.id == run_id).with_for_update())
        if run is None or run.api_key_id != key_id:
            raise HTTPException(404, "run not found")
        if states.can_transition(run.status, "cancelled"):
            run.status = "cancelled"
            run.termination_reason = "cancelled"
            run.finished_at = datetime.now(UTC)
            s.add(RunEvent(run_id=run.id, kind="state", data={"status": "cancelled"}))
        return run  # already terminal: cancelling again is a no-op


SSE_POLL_SECONDS = 0.5
SSE_MAX_SECONDS = 30 * 60


def _events_after(run_id: uuid.UUID, last_id: int) -> list[RunEvent]:
    with Session(get_engine(), expire_on_commit=False) as s:
        return list(s.scalars(select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.id > last_id)
                              .order_by(RunEvent.id).limit(500)))


def _run_status(run_id: uuid.UUID) -> str:
    with Session(get_engine()) as s:
        return s.get_one(Run, run_id).status


def sse_stream(run_id: uuid.UUID, last_id: int) -> Iterator[str]:
    """Server-Sent Events from the events table (the API polls the database: no LISTEN/NOTIFY,
    so it works through a transaction-mode pooler, ADR 2). Ends once the run is terminal and
    every event has been delivered."""
    deadline, last_beat = time.monotonic() + SSE_MAX_SECONDS, time.monotonic()
    while time.monotonic() < deadline:
        # read the status BEFORE the events: if it was already terminal, the events read next
        # include everything the dispatcher wrote (it persists logs before the final state)
        terminal = states.is_terminal(_run_status(run_id))
        batch = _events_after(run_id, last_id)
        for ev in batch:
            last_id = ev.id
            yield f"id: {ev.id}\nevent: {ev.kind}\ndata: {json.dumps({**ev.data, 'ts': ev.ts.isoformat()})}\n\n"
        if terminal and not batch:
            yield "event: end\ndata: {}\n\n"
            return
        if not batch:
            if time.monotonic() - last_beat > 15:
                yield ": keep-alive\n\n"
                last_beat = time.monotonic()
            time.sleep(SSE_POLL_SECONDS)


@app.get("/v1/runs/{run_id}/events")
def run_events(
    run_id: uuid.UUID,
    key_id: uuid.UUID = Depends(require_key),
    last_event_id: str | None = Header(default=None),
) -> StreamingResponse:
    with Session(get_engine()) as s:
        run = s.get(Run, run_id)
        if run is None or run.api_key_id != key_id:
            raise HTTPException(404, "run not found")
    start = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0
    return StreamingResponse(sse_stream(run_id, start), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
