"""Module 3 — lifecycle state machine schemas."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import Field

from .common import EngineModel, utcnow


class LifecycleState(str, Enum):
    CREATED = "created"
    IN_DEVELOPMENT = "in_development"
    TESTING = "testing"
    ROLLING_OUT = "rolling_out"
    FULLY_RELEASED = "fully_released"
    READY_FOR_CLEANUP = "ready_for_cleanup"
    CLEANUP_IN_PROGRESS = "cleanup_in_progress"
    RETIRED = "retired"


class EvidenceKind(str, Enum):
    CODE_REFERENCES_PRESENT = "code_references_present"
    ENABLED_IN_NON_PRODUCTION = "enabled_in_non_production"
    PRODUCTION_ROLLOUT_STARTED = "production_rollout_started"
    FULL_PRODUCTION_EXPOSURE = "full_production_exposure"
    ROLLOUT_STABLE = "rollout_stable"
    STALE_ASSESSMENT = "stale_assessment_ready_for_cleanup"
    OWNER_CONFIRMED_NOT_NEEDED = "owner_confirmed_not_needed"
    CLEANUP_PLAN_APPROVED = "cleanup_plan_approved"
    CLEANUP_VERIFIED = "cleanup_verified"
    CLEANUP_MERGED = "cleanup_merged"
    NO_REMAINING_REFERENCES = "no_remaining_references"
    ROLLBACK_REASON = "rollback_reason"
    MANUAL_NOTE = "manual_note"


class ApprovalRole(str, Enum):
    FLAG_OWNER = "flag_owner"
    CODE_REVIEWER = "code_reviewer"
    RELEASE_MANAGER = "release_manager"


class Approval(EngineModel):
    approved_by: str = Field(min_length=1)
    role: ApprovalRole
    approved_at: datetime
    comment: str | None = None


class TransitionEvidence(EngineModel):
    kind: EvidenceKind
    description: str
    source: str = Field(description="'code_scan', 'flagsmith', 'stale_detector', 'verification', 'github', 'owner', 'manual'.")
    reference: str | None = Field(default=None, description="Id/URL of the supporting artifact (scan id, verification id, PR URL...).")
    observed_at: datetime | None = None


class TransitionRule(EngineModel):
    from_state: LifecycleState
    to_state: LifecycleState
    description: str
    required_evidence: list[EvidenceKind] = Field(default_factory=list, description="All must be present.")
    any_of_evidence: list[EvidenceKind] = Field(default_factory=list, description="At least one must be present (if non-empty).")
    required_approvals: list[ApprovalRole] = Field(default_factory=list)
    is_rollback: bool = False


class TransitionRecord(EngineModel):
    id: str
    flag_id: str
    from_state: LifecycleState
    to_state: LifecycleState
    at: datetime
    actor: str
    reason: str
    evidence: list[TransitionEvidence] = Field(default_factory=list)
    approvals: list[Approval] = Field(default_factory=list)
    is_manual_override: bool = False
    override_justification: str | None = None


class FlagLifecycle(EngineModel):
    flag_id: str
    flag_name: str
    state: LifecycleState = LifecycleState.CREATED
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    history: list[TransitionRecord] = Field(default_factory=list)


class TransitionCheck(EngineModel):
    to_state: LifecycleState
    allowed: bool
    is_rollback: bool = False
    description: str
    missing_evidence: list[EvidenceKind] = Field(default_factory=list)
    missing_any_of: list[EvidenceKind] = Field(default_factory=list)
    missing_approvals: list[ApprovalRole] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)


class LifecycleEvaluation(EngineModel):
    flag_id: str
    flag_name: str
    current_state: LifecycleState
    suggested_state: LifecycleState
    suggestion_confidence: float = Field(ge=0, le=1)
    is_recommendation: Literal[True] = Field(
        default=True, description="Always true: a suggestion is never an applied transition.")
    rationale: list[str] = Field(default_factory=list)
    available_evidence: list[TransitionEvidence] = Field(default_factory=list)
    transitions: list[TransitionCheck] = Field(default_factory=list, description="Valid next transitions from the current state and what they still need.")
    warnings: list[str] = Field(default_factory=list)
    evaluated_at: datetime = Field(default_factory=utcnow)
