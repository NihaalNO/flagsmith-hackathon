"""A/B experiment analysis schemas (fixed-horizon, conversion-rate metrics)."""

from __future__ import annotations

from enum import Enum

from pydantic import Field

from .common import EngineModel


class VariantInput(EngineModel):
    key: str = Field(min_length=1, max_length=100)
    is_control: bool = False
    expected_share: float | None = Field(default=None, ge=0, le=100,
                                         description="Planned traffic share in percent (for the sample-ratio check).")
    exposures: int = Field(ge=0, description="Unique subjects who saw this variant.")
    conversions: int = Field(ge=0, description="Unique exposed subjects who converted.")


class ExperimentDesign(EngineModel):
    alpha: float = Field(default=0.05, gt=0, lt=0.5)
    power: float = Field(default=0.8, gt=0.5, lt=1)
    minimum_detectable_effect: float = Field(default=0.1, gt=0, le=5,
                                             description="Smallest relative lift worth detecting (0.1 = +10%).")
    min_exposures_per_variant: int = Field(default=100, ge=1)
    srm_alpha: float = Field(default=0.001, gt=0, lt=0.1)


class Decision(str, Enum):
    KEEP_RUNNING = "keep_running"              # not enough data yet
    TRENDING = "trending"                      # significant before the planned sample size: don't stop yet
    WINNER = "winner"                          # significant positive lift at the planned sample size
    CONTROL_BETTER = "control_better"          # every treatment significantly worse
    NO_SIGNIFICANT_DIFFERENCE = "no_significant_difference"
    INVALID = "invalid"                        # sample-ratio mismatch or broken data


class VariantResult(EngineModel):
    key: str
    is_control: bool
    exposures: int
    conversions: int
    conversion_rate: float | None
    rate_ci_low: float | None = Field(description="95% Wilson interval for the rate.")
    rate_ci_high: float | None
    absolute_lift: float | None = Field(default=None, description="Treatment rate minus control rate.")
    relative_lift: float | None = None
    lift_ci_low: float | None = Field(default=None, description="Confidence interval of the absolute lift (1 - alpha).")
    lift_ci_high: float | None = None
    p_value: float | None = Field(default=None, description="Two-sided two-proportion z-test vs control.")
    p_value_adjusted: float | None = Field(default=None, description="Holm-adjusted across treatments.")
    significant: bool = False
    probability_to_beat_control: float | None = Field(default=None, description="Bayesian, Beta(1,1) priors.")
    required_exposures: int | None = Field(default=None, description="Planned sample size per variant for the MDE.")


class SampleRatioCheck(EngineModel):
    checked: bool
    chi_square: float | None = None
    p_value: float | None = None
    mismatch: bool = False
    detail: str


class ExperimentAnalysis(EngineModel):
    decision: Decision
    winner: str | None = None
    summary: str
    variants: list[VariantResult]
    sample_ratio: SampleRatioCheck
    total_exposures: int
    total_conversions: int
    progress: float = Field(ge=0, le=1, description="Share of the planned sample collected (smallest variant).")
    warnings: list[str] = Field(default_factory=list)
    design: ExperimentDesign
