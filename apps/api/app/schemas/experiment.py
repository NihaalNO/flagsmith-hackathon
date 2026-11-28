from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

KEY_PATTERN = r"^[A-Za-z0-9_.\-]{1,100}$"


class ExperimentVariant(BaseModel):
    key: str = Field(pattern=KEY_PATTERN, description="What your app reports as the variant (e.g. the flag value).")
    name: str | None = Field(default=None, max_length=255)
    is_control: bool = False
    expected_share: float | None = Field(default=None, ge=0, le=100, description="Planned traffic share in percent.")
    flagsmith_option_id: int | None = None


class ExperimentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    hypothesis: str | None = Field(default=None, max_length=2000)
    primary_metric: str = Field(pattern=r"^[A-Za-z0-9_.\-]{1,100}$", examples=["checkout_completed"])
    environment_name: str | None = Field(default=None, max_length=255)
    variants: list[ExperimentVariant] | None = Field(
        default=None, min_length=2, max_length=10,
        description="Omit to use the flag's multivariate options from Flagsmith (control = the flag's own value).")
    minimum_detectable_effect: float = Field(default=0.1, ge=0.01, le=5, description="Relative lift worth detecting (0.1 = +10%).")
    alpha: float = Field(default=0.05, ge=0.001, le=0.2)


class ExperimentUpdate(BaseModel):
    """Name and hypothesis can change any time; the design only while the experiment is a draft."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    hypothesis: str | None = Field(default=None, max_length=2000)
    primary_metric: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.\-]{1,100}$")
    environment_name: str | None = Field(default=None, max_length=255)
    variants: list[ExperimentVariant] | None = Field(default=None, min_length=2, max_length=10)
    minimum_detectable_effect: float | None = Field(default=None, ge=0.01, le=5)
    alpha: float | None = Field(default=None, ge=0.001, le=0.2)


class ConcludeRequest(BaseModel):
    winner: str | None = Field(default=None, pattern=KEY_PATTERN, description="Variant to roll out, or null for none.")
    note: str | None = Field(default=None, max_length=2000)


class ExperimentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    flag_id: str
    flag_name: str | None = None
    name: str
    hypothesis: str | None
    primary_metric: str
    environment_name: str | None
    variants: list[ExperimentVariant]
    minimum_detectable_effect: float
    alpha: float
    status: str = Field(description="draft | running | stopped | concluded")
    started_at: datetime | None
    stopped_at: datetime | None
    concluded_at: datetime | None
    winner: str | None
    conclusion_note: str | None
    created_by: str | None
    created_at: datetime
    results: dict | None = Field(default=None, description="Engine statistics (see lifecycle_engine.schemas.experiments).")
    results_error: str | None = None


class ExperimentWithKey(ExperimentResponse):
    ingest_key: str = Field(description="Shown once. Your app sends it as X-Experiment-Key when reporting events.")
    ingest_example: str


class ExperimentEvent(BaseModel):
    type: Literal["exposure", "conversion"]
    subject_id: str = Field(min_length=1, max_length=200, description="Stable user/session id; only a hash is stored.")
    variant: str | None = Field(default=None, pattern=KEY_PATTERN, description="Required for exposures.")


class ExperimentEvents(BaseModel):
    events: list[ExperimentEvent] = Field(min_length=1, max_length=1000)


class ExperimentEventsResult(BaseModel):
    accepted_exposures: int
    accepted_conversions: int
    duplicates: int = Field(description="Repeat exposures/conversions of the same subject (counted once).")
    not_exposed: int = Field(description="Conversions from subjects with no exposure (ignored).")
    variant_conflicts: int = Field(description="Subjects reported in a second variant (first one kept).")
    unknown_variants: int = Field(description="Exposures for a variant the experiment doesn't have (ignored).")


# --- AI-proposed experiments on a repository ---------------------------------------------------

class ProposalValue(BaseModel):
    key: str = Field(pattern=KEY_PATTERN, description="What the app reports as the variant")
    value: str = Field(min_length=1, max_length=200, description="The flag value served in Flagsmith")
    description: str = Field(default="", max_length=500)


class ExperimentProposal(BaseModel):
    flag_name: str = Field(pattern=r"^[a-z][a-z0-9_]{2,59}$")
    title: str = Field(min_length=1, max_length=200)
    hypothesis: str = Field(default="", max_length=2000)
    primary_metric: str = Field(pattern=r"^[A-Za-z0-9_.\-]{1,100}$")
    control: ProposalValue
    variants: list[ProposalValue] = Field(min_length=1, max_length=3)
    files: list[str] = Field(default_factory=list, max_length=5)
    rationale: str = Field(default="", max_length=1000)


class ProposeRequest(BaseModel):
    focus: str | None = Field(default=None, max_length=300,
                              description="Optional steer, e.g. 'signup flow' or 'pricing page'")


class ProposalsResponse(BaseModel):
    repository: str
    commit_sha: str
    llm: str = Field(description="provider:model that wrote the proposals")
    proposals: list[ExperimentProposal]


class LaunchRequest(BaseModel):
    proposals: list[ExperimentProposal] = Field(min_length=1, max_length=3)
    start: bool = Field(default=True, description="Start collecting events right away")
    minimum_detectable_effect: float = Field(default=0.1, ge=0.01, le=5)


class LaunchResult(BaseModel):
    launched: list[ExperimentWithKey]
    errors: list[str] = Field(description="Proposals that couldn't be launched, and why (the others still were)")
