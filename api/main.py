import uuid

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from api.auth import require_key
from api.db import get_engine
from api.models import Run, RunEvent
from api.schemas import MAX_CODE_BYTES, SUPPORTED, RunCreate, RunOut

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


@app.post("/v1/runs", status_code=202, response_model=RunOut)
def create_run(
    body: RunCreate,
    key_id: uuid.UUID = Depends(require_key),
    idempotency_key: str | None = Header(default=None),
) -> Run:
    validate(body)
    with Session(get_engine(), expire_on_commit=False) as s, s.begin():
        run_id = uuid.uuid4()
        stmt = (
            insert(Run)
            .values(id=run_id, api_key_id=key_id, idempotency_key=idempotency_key,
                    language=body.language, code=body.code, stdin=body.stdin,
                    timeout_seconds=body.timeout_seconds)
            .on_conflict_do_nothing(constraint="uq_runs_idempotency")
            .returning(Run.id)
        )
        inserted = s.execute(stmt).scalar_one_or_none()
        if inserted is None:  # same Idempotency-Key seen before
            existing = s.scalar(select(Run).where(Run.api_key_id == key_id,
                                                  Run.idempotency_key == idempotency_key))
            assert existing is not None
            same = (existing.language, existing.code, existing.stdin, existing.timeout_seconds) == (
                body.language, body.code, body.stdin, body.timeout_seconds)
            if not same:
                raise HTTPException(409, "Idempotency-Key reused with a different request")
            return existing
        s.add(RunEvent(run_id=run_id, kind="state", data={"status": "queued"}))
        s.flush()
        return s.get_one(Run, run_id)


@app.get("/v1/runs/{run_id}", response_model=RunOut)
def get_run(run_id: uuid.UUID, key_id: uuid.UUID = Depends(require_key)) -> Run:
    with Session(get_engine(), expire_on_commit=False) as s:
        run = s.get(Run, run_id)
        if run is None or run.api_key_id != key_id:
            raise HTTPException(404, "run not found")
        return run
