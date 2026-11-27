from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.lifecycle import LifecycleStatus


class FlagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    owner: str | None = None
    project_name: str | None = None
    environment: str | None = None
    enabled: bool = False
    integration_id: str | None = Field(
        default=None,
        description="If set, the feature is created in this Flagsmith integration's project first",
    )
    repository_id: str | None = Field(
        default=None,
        description="If set, the feature is created in this repository's Flagsmith project first",
    )


class FlagUpdate(BaseModel):
    """Partial update. Only fields that are sent are changed."""

    description: str | None = Field(default=None, max_length=1000)
    owner: str | None = None
    project_name: str | None = None
    environment: str | None = None
    enabled: bool | None = None


class LifecycleTransition(BaseModel):
    status: LifecycleStatus
    reason: str | None = Field(default=None, max_length=1000)


class FlagResponse(BaseModel):
    id: str
    name: str
    description: str | None
    flagsmith_id: str | None
    integration_id: str | None
    flagsmith_project_id: int | None
    owner: str | None
    project_name: str | None
    environment: str | None
    lifecycle_status: LifecycleStatus
    lifecycle_changed_at: datetime
    enabled: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
