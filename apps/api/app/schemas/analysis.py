from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AnalysisRepository(BaseModel):
    full_name: str
    commit_sha: str


class FlagAnalysisSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    flag_id: str
    engine_version: str
    stale_status: str = Field(description="not_stale | insufficient_evidence | potentially_stale | ready_for_cleanup | safe_to_retire")
    stale_confidence: float
    cleanup_direction: str
    suggested_state: str = Field(description="Lifecycle state the engine suggests. A recommendation only; never applied.")
    recommended_action: str
    confirmed_references: int
    potential_references: int
    dynamic_references: int
    llm_status: str
    created_by: str | None
    created_at: datetime


class FlagAnalysisResponse(FlagAnalysisSummary):
    repositories: list[AnalysisRepository]
    result: dict = Field(description=(
        "Full engine output: analysis, stale_finding, lifecycle_evaluation, cleanup_plan, patch, "
        "flag_configuration, scan_errors. Field guide: services/lifecycle-engine/docs/api-contract.md"))


class AnalyzeRequest(BaseModel):
    use_llm: bool = Field(default=True, description="Use the configured LLM provider for the plan narrative, if any.")
