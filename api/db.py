"""Database engine. Works through a transaction-mode pooler (no session state, no LISTEN/NOTIFY)."""
import os

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


_engine: Engine | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        # prepare_threshold=None: no server-side prepared statements (PgBouncer transaction mode).
        _engine = create_engine(
            database_url(), pool_pre_ping=True, connect_args={"prepare_threshold": None}
        )
    return _engine
