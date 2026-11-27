from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Provider(StrEnum):
    FLAGSMITH = "flagsmith"
    GITHUB = "github"


class IntegrationConfig(BaseModel):
    """Non-secret settings. Never put tokens here; use `api_token`."""

    base_url: str | None = Field(default=None, description="Self-hosted API URL, e.g. https://flags.acme.com/api/v1")
    project_id: int | None = Field(default=None, description="Flagsmith project to sync")
    environment_key: str | None = Field(default=None, description="Flagsmith environment api_key whose on/off state is mirrored")

    model_config = ConfigDict(extra="allow")


class IntegrationCreate(BaseModel):
    provider: Provider
    name: str = Field(min_length=1, max_length=255)
    config: IntegrationConfig = Field(default_factory=IntegrationConfig)
    api_token: str | None = Field(default=None, min_length=1, description="Write-only; stored encrypted")


class IntegrationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    config: IntegrationConfig | None = None
    api_token: str | None = Field(default=None, min_length=1, description="Write-only; replaces stored key")


class IntegrationResponse(BaseModel):
    id: str
    provider: Provider
    name: str
    status: str
    status_detail: str | None
    config: dict
    has_secret: bool
    last_synced_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConnectionTestResult(BaseModel):
    ok: bool
    detail: str
    organisations: list[str] = []


class FlagsmithProject(BaseModel):
    id: int
    name: str
    organisation: int | None = None


class FlagsmithEnvironment(BaseModel):
    id: int
    name: str
    api_key: str


class SyncResult(BaseModel):
    projects: list[str] = Field(default=[], description="Flagsmith projects that were synced")
    created: int
    linked: int
    updated: int
    unchanged: int
    conflicts: list[str] = Field(description="Flagsmith features whose name is taken by a different linked flag")
    missing_in_flagsmith: list[str] = Field(description="Linked flags whose feature no longer exists in Flagsmith")
