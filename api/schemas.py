import os
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

MAX_CODE_BYTES = 64 * 1024
LANGUAGES = ("python3.12", "node20")
SUPPORTED = tuple(os.environ.get("KSC_LANGUAGES", "python3.12,node20").split(","))


class RunCreate(BaseModel):
    language: Literal["python3.12", "node20"]
    code: str = Field(min_length=1)
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    stdin: str | None = Field(default=None, max_length=MAX_CODE_BYTES)


class RunOut(BaseModel):
    id: uuid.UUID
    language: str
    status: str
    timeout_seconds: int
    exit_code: int | None
    termination_reason: str | None
    stdout: str
    stderr: str
    truncated: bool
    node: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class BatchCreate(BaseModel):
    runs: list[RunCreate] = Field(min_length=1, max_length=20)


class BatchOut(BaseModel):
    runs: list[RunOut]
