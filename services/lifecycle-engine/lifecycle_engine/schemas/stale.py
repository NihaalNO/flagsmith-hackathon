"""Module 4 — stale flag detection schemas."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import Field

from .common import EngineModel, EvidenceItem, MissingEvidence, utcnow
from .lifecycle import LifecycleState


class StaleStatus(str, Enum):
    NOT_STALE = "not_stale"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    POTENTIALLY_STALE = "potentially_stale"
    READY_FOR_CLEANUP = "ready_for_cleanup"
    SAFE_TO_RETIRE = "safe_to_retire"


STATUS_ORDER = [
    StaleStatus.NOT_STALE,
    StaleStatus.INSUFFICIENT_EVIDENCE,
    StaleStatus.POTENTIALLY_STALE,
    StaleStatus.READY_FOR_CLEANUP,
    StaleStatus.SAFE_TO_RETIRE,
]


class CleanupDirection(str, Enum):
    KEEP_ENABLED_PATH = "keep_enabled_path"
    KEEP_DISABLED_PATH = "keep_disabled_path"
    INLINE_VALUE = "inline_value"
    ARCHIVE_ONLY = "archive_only"   # no code references; cleanup is a Flagsmith archive (after approval)
    UNDETERMINED = "undetermined"


class StalePolicy(EngineModel):
    id: str = "default"
    version: str = "1"
    min_age_days: int = Field(default=30, ge=0, description="Younger flags are not considered stale. Age is never proof by itself.")
    stable_config_days: int = Field(default=14, ge=0, description="Production config must be unchanged this long.")
    activity_window_days: int = Field(default=30, ge=1)
    potential_threshold: float = Field(default=0.3, description="Net evidence score needed for POTENTIALLY_STALE.")
    require_owner_confirmation: bool = Field(default=True, description="READY_FOR_CLEANUP requires an owner statement.")
    long_lived_tags: list[str] = Field(default_factory=lambda: ["permanent", "kill-switch", "ops", "permission", "remote-config"])
    weights: dict[str, float] = Field(default_factory=lambda: {
        "age": 0.1,
        "young": 0.4,
        "uniform_production": 0.25,
        "targeting_active": 0.6,
        "environments_agree": 0.1,
        "config_stable": 0.15,
        "config_recently_changed": 0.35,
        "owner_not_needed": 0.3,
        "lifecycle_released": 0.1,
        "lifecycle_active": 0.3,
        "evaluated_by_unscanned_consumer": 0.4,
        "zero_references": 0.05,
    })


class Blocker(EngineModel):
    kind: str
    description: str
    caps_status_at: StaleStatus


class StaleFlagFinding(EngineModel):
    id: str
    flag_id: str
    flag_name: str
    status: StaleStatus
    confidence: float = Field(ge=0, le=1)
    score: float = Field(description="Net weighted evidence score (supports minus opposes).")
    cleanup_direction: CleanupDirection
    inline_value: str | int | float | bool | None = None
    lifecycle_state: LifecycleState | None = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
    missing_evidence: list[MissingEvidence] = Field(default_factory=list)
    blockers: list[Blocker] = Field(default_factory=list)
    uncertainty: list[str] = Field(default_factory=list)
    recommended_next_action: str
    requires_human_decision: Literal[True] = Field(
        default=True, description="Always true: a finding is a recommendation, never a deletion decision.")
    policy_id: str
    policy_version: str
    evaluated_at: datetime = Field(default_factory=utcnow)
