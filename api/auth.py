import hashlib
import secrets
import uuid

from fastapi import Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.db import get_engine
from api.models import ApiKey


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def new_key() -> str:
    return "ksc_" + secrets.token_urlsafe(32)


def require_key(authorization: str | None = Header(default=None)) -> uuid.UUID:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    digest = hash_key(authorization[7:].strip())
    with Session(get_engine()) as s:
        row = s.scalar(select(ApiKey).where(ApiKey.key_hash == digest))
        if row is None:
            raise HTTPException(401, "invalid api key")
        return row.id
