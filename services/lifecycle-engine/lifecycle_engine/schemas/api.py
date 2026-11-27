"""Request/response contracts for the engine's callable operations (and HTTP routes)."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from .analysis import FlagAnalysisReport
from .common import EngineModel
from .discovery import RepositoryTarget
from .flags import ConsumerCoverage, DynamicReferenceReview, FlagConfiguration
from .lifecycle import Approval, FlagLifecycle, LifecycleState, TransitionEvidence
from .patch import CleanupPatch
from .planning import CleanupPlan
from .stale import CleanupDirection, StaleFlagFinding, StalePolicy, StaleStatus
from .verification import CommandSpec


class _RepoRequest(EngineModel):
    repositories: list[RepositoryTarget] = Field(min_length=1)


class DiscoverRequest(_RepoRequest):
    pass


class AnalyzeFlagRequest(_RepoRequest):
    flag_name: str
    configuration: FlagConfiguration | None = None


class EvaluateLifecycleRequest(_RepoRequest):
    flag_name: str
    configuration: FlagConfiguration | None = None
    lifecycle: FlagLifecycle | None = None
    coverage: ConsumerCoverage | None = None
    policy: StalePolicy | None = None
    as_of: datetime | None = Field(default=None, description="Evaluation time; defaults to now.")


class TransitionRequest(EngineModel):
    lifecycle: FlagLifecycle
    to_state: LifecycleState
    actor: str
    reason: str
    evidence: list[TransitionEvidence] = Field(default_factory=list)
    approvals: list[Approval] = Field(default_factory=list)
    override: bool = Field(default=False, description="Manual override (audited; cannot bypass cleanup/retire safety).")


class DetectStaleRequest(_RepoRequest):
    configurations: list[FlagConfiguration]
    lifecycles: dict[str, FlagLifecycle] = Field(default_factory=dict, description="flag_name -> lifecycle record")
    coverage: ConsumerCoverage | None = None
    policy: StalePolicy | None = None
    include_unconfigured_code_flags: bool = Field(default=True, description="Also evaluate flags found in code but absent from config.")
    as_of: datetime | None = None


class StaleDetectionResponse(EngineModel):
    scan_id: str
    findings: list[StaleFlagFinding]
    counts: dict[StaleStatus, int]


class CleanupPlanRequest(_RepoRequest):
    flag_name: str
    configuration: FlagConfiguration | None = None
    lifecycle: FlagLifecycle | None = None
    coverage: ConsumerCoverage | None = None
    policy: StalePolicy | None = None
    include_patch: bool = Field(default=True, description="Generate a suggested patch when the flag is ready for cleanup.")
    use_llm: bool = True
    as_of: datetime | None = None


class CleanupPlanResponse(EngineModel):
    plan: CleanupPlan
    stale_finding: StaleFlagFinding
    analysis: FlagAnalysisReport
    patch: CleanupPatch | None = None


class GeneratePatchRequest(_RepoRequest):
    flag_name: str
    direction: CleanupDirection | None = Field(default=None, description="Derived from configuration when omitted.")
    inline_value: str | int | float | bool | None = None
    configuration: FlagConfiguration | None = None


class VerifyCleanupRequest(_RepoRequest):
    patch: CleanupPatch
    commands: list[CommandSpec] = Field(default_factory=list, description="Only honoured when the service allows request commands.")
    auto_detect_tests: bool = True
    require_tests: bool = True
    dynamic_reference_reviews: list[DynamicReferenceReview] = Field(default_factory=list)
