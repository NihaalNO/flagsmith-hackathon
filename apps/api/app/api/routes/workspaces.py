from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser, get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models.workspace import Workspace, WorkspaceMember
from app.services.github import GitHubClientFactory, GitHubError, get_github_client_factory
from app.services.workspaces import discover_workspaces

router = APIRouter(prefix="/workspaces", tags=["Workspaces"])


class WorkspaceOut(BaseModel):
    id: str
    name: str
    github_account_login: str
    github_account_type: str
    role: str


class WorkspaceList(BaseModel):
    workspaces: list[WorkspaceOut]
    install_url: str | None
    warnings: list[str] = []


@router.get("", response_model=WorkspaceList)
def list_workspaces(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    make_client: GitHubClientFactory = Depends(get_github_client_factory),
):
    """Your workspaces, re-checked against GitHub: one per account/org with the app installed
    that you own or belong to. Empty list -> send the user to `install_url`."""
    try:
        with make_client() as gh:
            _, warnings = discover_workspaces(db, user, gh)
    except GitHubError as exc:
        raise HTTPException(502, f"GitHub: {exc}")
    rows = db.execute(
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id).order_by(Workspace.name)
    ).all()
    install_url = (f"https://github.com/apps/{settings.github_app_slug}/installations/new"
                   if settings.github_app_slug else None)
    return {"workspaces": [{"id": w.id, "name": w.name, "github_account_login": w.github_account_login,
                            "github_account_type": w.github_account_type, "role": role} for w, role in rows],
            "install_url": install_url, "warnings": warnings}
