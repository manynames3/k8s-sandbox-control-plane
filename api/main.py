from fastapi import FastAPI, Response
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from api.db import get_engine

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
