"""Flag verdict: is a flag active, and is it still useful?"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import Field

from .common import EngineModel, utcnow
from .planning import EvidenceRef, LLMUsage, ReasonedStatement


class Activity(str, Enum):
    ACTIVE = "active"        # something evaluates or operates the flag
    INACTIVE = "inactive"    # nothing evaluates it, it's off everywhere, or it's archived
    UNKNOWN = "unknown"


class Usefulness(str, Enum):
    USEFUL = "useful"            # it still makes a difference (targeting, experiment, rollout, kill switch...)
    NOT_USEFUL = "not_useful"    # every user gets the same behaviour, or nothing uses it
    UNCERTAIN = "uncertain"


class VerdictReason(EngineModel):
    text: str
    points_to: str = Field(description="The label this reason supports, e.g. 'active' or 'not_useful'.")
    evidence_ids: list[str] = Field(default_factory=list)


class ExperimentStatusInput(EngineModel):
    name: str
    status: str = Field(description="draft | running | stopped | concluded")
    decision: str | None = None


class FlagVerdict(EngineModel):
    flag_name: str
    activity: Activity
    usefulness: Usefulness
    confidence: float = Field(ge=0, le=1)
    activity_reasons: list[VerdictReason] = Field(default_factory=list)
    usefulness_reasons: list[VerdictReason] = Field(default_factory=list)
    recommendation: str
    explanation: str = Field(description="Plain-language summary; written by the LLM when one is configured and its output is valid.")
    statements: list[ReasonedStatement] = Field(default_factory=list, description="Fact / hypothesis / recommendation statements with citations.")
    evidence: list[EvidenceRef] = Field(default_factory=list, description="Everything the reasons and statements may cite.")
    caveats: list[str] = Field(default_factory=list)
    llm: LLMUsage
    is_recommendation: Literal[True] = True
    evaluated_at: datetime = Field(default_factory=utcnow)
