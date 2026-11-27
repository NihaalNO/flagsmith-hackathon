"""Module 6 — cleanup verification schemas."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field, model_validator

from .common import EngineModel, utcnow
from .discovery import FlagReference
from .lifecycle import ApprovalRole


class CheckStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"        # could not run (missing tool, timeout, crash) - never counts as passing
    SKIPPED = "skipped"


class CommandSpec(EngineModel):
    name: str
    kind: str = Field(description="'tests' or 'static'.")
    repository: str
    argv: list[str] = Field(min_length=1)
    cwd: str = Field(default=".", description="Relative to the repository root.")
    timeout_s: int = Field(default=300, ge=1)


class CommandResult(EngineModel):
    name: str
    kind: str
    repository: str
    argv: list[str]
    status: CheckStatus
    exit_code: int | None = None
    duration_s: float = 0.0
    output_tail: str = ""
    error: str | None = None


class StaticFinding(EngineModel):
    check: str
    repository: str
    file_path: str
    line: int | None = None
    message: str


class VerificationStatus(str, Enum):
    FAILED = "failed"
    INCOMPLETE = "incomplete"
    READY_FOR_REVIEW = "ready_for_review"


class VerificationResult(EngineModel):
    id: str
    patch_id: str
    flag_name: str
    files_changed: list[str]
    patch_applied: bool
    apply_errors: list[str] = Field(default_factory=list)
    remaining_references: list[FlagReference] = Field(default_factory=list)
    unresolved_dynamic_references: list[FlagReference] = Field(default_factory=list)
    static_findings: list[StaticFinding] = Field(default_factory=list)
    test_results: list[CommandResult] = Field(default_factory=list)
    static_results: list[CommandResult] = Field(default_factory=list)
    status: VerificationStatus
    ready_for_human_review: bool
    unresolved_risks: list[str] = Field(default_factory=list)
    required_approvals: list[ApprovalRole] = Field(default_factory=list)
    actions_not_taken: list[str] = Field(default_factory=list)
    workspace_path: str | None = Field(default=None, description="Set only when the isolated workspace was retained.")
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _failures_cannot_pass(self):
        failed = (
            not self.patch_applied
            or self.apply_errors
            or self.static_findings
            or any(r.status in (CheckStatus.FAILED, CheckStatus.ERROR) for r in [*self.test_results, *self.static_results])
        )
        if failed and self.status != VerificationStatus.FAILED:
            raise ValueError("a verification with failed or errored checks must have status 'failed'")
        if self.ready_for_human_review and self.status != VerificationStatus.READY_FOR_REVIEW:
            raise ValueError("ready_for_human_review requires status 'ready_for_review'")
        if self.status == VerificationStatus.READY_FOR_REVIEW and (self.remaining_references or self.unresolved_dynamic_references):
            raise ValueError("cleanup with remaining references cannot be ready for review")
        return self
