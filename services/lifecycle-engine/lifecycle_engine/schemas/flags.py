"""Flag configuration and activity inputs.

These describe what the engine *receives* about a flag from Flagsmith (via
Member 1's integration) or from mock data. The engine never fetches them itself.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field

from .common import EngineModel


class FeatureType(str, Enum):
    STANDARD = "standard"
    MULTIVARIATE = "multivariate"


class FlagPurpose(str, Enum):
    RELEASE = "release"
    EXPERIMENT = "experiment"
    OPS_KILL_SWITCH = "ops_kill_switch"
    PERMISSION = "permission"
    REMOTE_CONFIG = "remote_config"


# Purposes that are expected to live indefinitely; never treated as stale by age.
LONG_LIVED_PURPOSES = {FlagPurpose.OPS_KILL_SWITCH, FlagPurpose.PERMISSION, FlagPurpose.REMOTE_CONFIG}


class EnvironmentFlagState(EngineModel):
    environment: str
    is_production: bool = False
    enabled: bool
    value: str | int | float | bool | None = None
    segment_overrides: int = Field(default=0, ge=0)
    identity_overrides: int = Field(default=0, ge=0)
    percentage_allocation: float | None = Field(
        default=None, ge=0, le=100, description="Multivariate/percentage split; None or 100 means no split."
    )
    updated_at: datetime | None = None

    @property
    def is_uniform(self) -> bool:
        """True when every identity in this environment gets the same state."""
        return (
            self.segment_overrides == 0
            and self.identity_overrides == 0
            and (self.percentage_allocation is None or self.percentage_allocation in (0, 100))
        )


class ConfigChange(EngineModel):
    at: datetime
    environment: str | None = None
    description: str


class FlagActivity(EngineModel):
    source: str = Field(description="e.g. 'flagsmith_analytics', 'mock'.")
    window_days: int = Field(ge=1)
    evaluation_count: int | None = Field(default=None, ge=0)
    last_evaluated_at: datetime | None = None


class OwnerConfirmation(EngineModel):
    confirmed_by: str
    confirmed_at: datetime
    purpose: FlagPurpose
    still_needed: bool | None = Field(default=None, description="Owner's statement whether the flag is still needed.")
    notes: str | None = None


class FlagConfiguration(EngineModel):
    flag_name: str
    project: str
    flagsmith_feature_id: int | None = None
    description: str | None = None
    created_at: datetime | None = None
    owners: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    is_archived: bool = False
    feature_type: FeatureType = FeatureType.STANDARD
    environments: list[EnvironmentFlagState] = Field(default_factory=list)
    recent_changes: list[ConfigChange] = Field(default_factory=list)
    activity: FlagActivity | None = None
    owner_confirmation: OwnerConfirmation | None = None
    data_source: str = Field(default="mock", description="'flagsmith' when fetched live, 'mock' for sample data.")
    fetched_at: datetime | None = None

    @property
    def flag_id(self) -> str:
        return f"{self.project}:{self.flag_name}"

    @property
    def production(self) -> list[EnvironmentFlagState]:
        return [e for e in self.environments if e.is_production]

    def uniform_production_state(self) -> bool | None:
        """Enabled/disabled state shared by all identities in all production
        environments, or None if production is split, mixed or unknown."""
        prod = self.production
        if not prod or not all(e.is_uniform for e in prod):
            return None
        states = {e.enabled for e in prod}
        return states.pop() if len(states) == 1 else None

    def uniform_production_value(self) -> tuple[bool, str | int | float | bool | None]:
        """(is_uniform, value) for the remote-config value in production."""
        prod = self.production
        if not prod or not all(e.is_uniform for e in prod):
            return False, None
        values = {repr(e.value) for e in prod}
        return (len(values) == 1, prod[0].value)


class DynamicReferenceReview(EngineModel):
    """A human's statement that a dynamic reference cannot evaluate certain flags."""

    reference_id: str
    reviewed_by: str
    reviewed_at: datetime
    excluded_flags: list[str] = Field(description="Flags this reference never evaluates; ['*'] for all.")
    notes: str | None = None

    def excludes(self, flag_name: str) -> bool:
        return "*" in self.excluded_flags or flag_name in self.excluded_flags


class ConsumerCoverage(EngineModel):
    """What is known about where a flag may be consumed outside the scanned code."""

    scanned_repositories: list[str] = Field(default_factory=list)
    all_consumers_scanned: bool | None = Field(
        default=None, description="True only when someone confirmed every consuming codebase was scanned."
    )
    known_external_consumers: list[str] = Field(
        default_factory=list, description="e.g. mobile apps, other services, data pipelines not scanned."
    )
    dynamic_reference_reviews: list[DynamicReferenceReview] = Field(default_factory=list)
