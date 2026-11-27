from datetime import datetime

from pydantic import BaseModel, ConfigDict


class InstallUrl(BaseModel):
    url: str


class GitHubSyncResult(BaseModel):
    installations: int
    repositories_added: int
    repositories_updated: int
    repositories: list[str]
    projects_created: list[str] = []
    projects_linked: list[str] = []
    project_errors: list[str] = []


class FlagScanCount(BaseModel):
    flag: str
    references: int


class ScanResponse(BaseModel):
    repository: str
    commit_sha: str
    files_scanned: int
    references: int
    flags: list[FlagScanCount]


class FlagReferenceResponse(BaseModel):
    id: str
    repository_id: str
    repository: str
    path: str
    line: int
    snippet: str
    commit_sha: str | None
    url: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
