from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RepositoryCreate(BaseModel):
    full_name: str = Field(min_length=3, max_length=255, pattern=r"^[\w.-]+/[\w.-]+$",
                           examples=["acme/web-app"])
    default_branch: str = Field(default="main", min_length=1, max_length=100)
    github_repo_id: str | None = None


class RepositoryResponse(BaseModel):
    id: str
    github_repo_id: str | None
    name: str
    full_name: str
    default_branch: str
    installation_id: str | None
    last_scanned_at: datetime | None
    last_scan_sha: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
