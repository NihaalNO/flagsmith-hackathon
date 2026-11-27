"""Module 5 — cleanup plan schemas."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import Field

from .common import EngineModel, MissingEvidence, SourceSpan, utcnow
from .lifecycle import ApprovalRole, LifecycleState
from .stale import CleanupDirection, StaleStatus


class RecommendedAction(str, Enum):
    NO_ACTION = "no_action"
    GATHER_EVIDENCE = "gather_evidence"
    REQUEST_OWNER_REVIEW = "request_owner_review"
    PREPARE_CLEANUP_PATCH = "prepare_cleanup_patch"
    REQUEST_ARCHIVE_APPROVAL = "request_archive_approval"


class StatementType(str, Enum):
    FACT = "fact"                  # must cite evidence
    HYPOTHESIS = "hypothesis"      # inference from evidence, may be wrong
    RECOMMENDATION = "recommendation"


class ReasonedStatement(EngineModel):
    type: StatementType
    text: str
    evidence_ids: list[str] = Field(default_factory=list)


class EvidenceRef(EngineModel):
    id: str
    kind: str
    summary: str
    location: SourceSpan | None = None


class ProposedChange(EngineModel):
    id: str
    repository: str
    file_path: str
    line: int
    reference_id: str | None = None
    change_kind: str = Field(description="EditKind value, or 'manual'.")
    description: str


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RiskItem(EngineModel):
    level: RiskLevel
    description: str
    evidence_ids: list[str] = Field(default_factory=list)
    mitigation: str | None = None


class RiskAssessment(EngineModel):
    overall: RiskLevel
    items: list[RiskItem] = Field(default_factory=list)


class VerificationRequirement(EngineModel):
    kind: str = Field(description="'tests', 'static_check', 'rescan' or 'manual_review'.")
    description: str
    repository: str | None = None
    target: str | None = None


class LLMStatus(str, Enum):
    NOT_CONFIGURED = "not_configured"
    USED = "used"
    UNAVAILABLE = "unavailable"
    REJECTED_INVALID_OUTPUT = "rejected_invalid_output"


class LLMUsage(EngineModel):
    status: LLMStatus
    provider: str | None = None
    model: str | None = None
    validation_errors: list[str] = Field(default_factory=list)
    error: str | None = None


class CleanupPlan(EngineModel):
    id: str
    flag_id: str
    flag_name: str
    lifecycle_state: LifecycleState | None
    stale_status: StaleStatus
    cleanup_direction: CleanupDirection
    recommended_action: RecommendedAction
    reasoning_summary: str
    statements: list[ReasonedStatement] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list, description="Everything statements may cite.")
    affected_files: list[str] = Field(default_factory=list, description="'repository:path' entries.")
    obsolete_code_paths: list[str] = Field(default_factory=list)
    proposed_changes: list[ProposedChange] = Field(default_factory=list)
    patch_id: str | None = None
    patch_rationale: str | None = None
    risk: RiskAssessment
    confidence: float = Field(ge=0, le=1)
    missing_evidence: list[MissingEvidence] = Field(default_factory=list)
    required_approvals: list[ApprovalRole] = Field(default_factory=list)
    verification_requirements: list[VerificationRequirement] = Field(default_factory=list)
    llm: LLMUsage
    is_recommendation: Literal[True] = True
    created_at: datetime = Field(default_factory=utcnow)


# --- The only structure an LLM is allowed to return -------------------------------

class LLMRisk(EngineModel):
    level: RiskLevel
    description: str
    evidence_ids: list[str]
    mitigation: str | None = None


class LLMPlanNarrative(EngineModel):
    """Narrative the LLM may contribute. It cannot add changes, files or states."""

    reasoning_summary: str
    statements: list[ReasonedStatement]
    risks: list[LLMRisk] = Field(default_factory=list)
    patch_rationale: str | None = None
