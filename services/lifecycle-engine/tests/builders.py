"""Shared builders for tests (deterministic clock, configs, lifecycles)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from lifecycle_engine.schemas.flags import (
    EnvironmentFlagState,
    FlagActivity,
    FlagConfiguration,
    FlagPurpose,
    OwnerConfirmation,
)
from lifecycle_engine.schemas.lifecycle import Approval, ApprovalRole, EvidenceKind, TransitionEvidence

from tests.conftest import SAMPLE_DATA

NOW = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)


def days_ago(n: int) -> datetime:
    return NOW - timedelta(days=n)


def sample_configs() -> dict[str, FlagConfiguration]:
    data = json.loads((SAMPLE_DATA / "flags.json").read_text(encoding="utf-8"))
    return {c["flag_name"]: FlagConfiguration.model_validate(c) for c in data["flags"]}


def released_config(name: str = "f", *, owner_says_done: bool = True, evaluations: int | None = 100,
                    enabled: bool = True, **kw) -> FlagConfiguration:
    kw.setdefault("created_at", days_ago(200))
    return FlagConfiguration(
        flag_name=name, project="p", owners=["owner@x"],
        environments=[
            EnvironmentFlagState(environment="staging", enabled=enabled, updated_at=days_ago(150)),
            EnvironmentFlagState(environment="production", is_production=True, enabled=enabled, updated_at=days_ago(100)),
        ],
        activity=FlagActivity(source="mock", window_days=30, evaluation_count=evaluations) if evaluations is not None else None,
        owner_confirmation=OwnerConfirmation(confirmed_by="owner@x", confirmed_at=days_ago(2), purpose=FlagPurpose.RELEASE,
                                             still_needed=False) if owner_says_done else None,
        **kw,
    )


def ev(kind: EvidenceKind, source: str = "manual", reference: str | None = None) -> TransitionEvidence:
    return TransitionEvidence(kind=kind, description=kind.value, source=source, reference=reference)


def approval(role: ApprovalRole, who: str = "alice") -> Approval:
    return Approval(approved_by=who, role=role, approved_at=NOW)
