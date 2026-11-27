"""Shared primitives used across every engine schema."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """Treat naive datetimes as UTC (some databases/drivers drop the timezone)."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def stable_id(prefix: str, *parts: object, length: int = 16) -> str:
    """Deterministic identifier: same inputs always yield the same id."""
    digest = hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:length]}"


class EngineModel(BaseModel):
    """Base for all engine schemas: strict field names, JSON-friendly enums."""

    model_config = ConfigDict(extra="forbid", use_enum_values=False, populate_by_name=True, validate_assignment=True)

    @field_validator("*", mode="after")
    @classmethod
    def _naive_datetimes_are_utc(cls, value):
        # Every timestamp the engine compares must be timezone-aware.
        return as_utc(value) if isinstance(value, datetime) else value


class SourceSpan(EngineModel):
    """A location in a repository file. Lines and columns are 1-based."""

    repository: str
    file_path: str = Field(description="POSIX path relative to the repository root.")
    line: int = Field(ge=1)
    column: int = Field(ge=1)
    end_line: int = Field(ge=1)
    end_column: int = Field(ge=1)
    start_byte: int | None = Field(default=None, ge=0, description="Byte offset in the scanned file content (internal use).")
    end_byte: int | None = Field(default=None, ge=0)


class EvidenceDirection(str, Enum):
    SUPPORTS = "supports"
    OPPOSES = "opposes"
    NEUTRAL = "neutral"


class EvidenceItem(EngineModel):
    """A single verifiable observation used to justify a conclusion."""

    id: str
    kind: str = Field(description="Machine-readable evidence kind, e.g. 'code_reference_count'.")
    source: str = Field(description="Where the observation came from: 'code_scan', 'flagsmith', 'owner', 'verification'...")
    description: str
    direction: EvidenceDirection = EvidenceDirection.NEUTRAL
    weight: float = Field(default=0.0, description="Contribution to the conclusion; sign follows direction.")
    value: str | int | float | bool | None = None
    observed_at: datetime | None = None


class MissingEvidence(EngineModel):
    kind: str
    description: str
    blocks: list[str] = Field(default_factory=list, description="Conclusions this missing evidence prevents.")


class ErrorDetail(EngineModel):
    code: str
    message: str
    details: dict = Field(default_factory=dict)


class ErrorResponse(EngineModel):
    error: ErrorDetail
