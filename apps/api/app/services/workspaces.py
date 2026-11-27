"""Decide which workspaces a signed-in user may use, verified against GitHub.

- App installed on a personal account -> only that GitHub user.
- App installed on an organization     -> every member of that GitHub org.
"""

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser
from app.models.base import utc_now
from app.models.workspace import Workspace, WorkspaceMember
from app.services.github import GitHubAppClient, GitHubError


def _has_access(gh: GitHubAppClient, inst: dict, user: CurrentUser) -> bool:
    account = inst["account"]
    if account.get("type") == "Organization":
        if not user.github_login:
            return False
        return gh.is_org_member(str(inst["id"]), account["login"], user.github_login)
    return user.github_id is not None and str(account["id"]) == user.github_id


def discover_workspaces(db: Session, user: CurrentUser, gh: GitHubAppClient) -> tuple[list[Workspace], list[str]]:
    """Create workspaces for installations, refresh this user's memberships. Returns (workspaces, warnings)."""
    warnings: list[str] = []
    mine: list[Workspace] = []
    for inst in gh.list_installations():
        inst_id, account = str(inst["id"]), inst["account"]
        ws = db.scalar(select(Workspace).where(Workspace.installation_id == inst_id))
        if ws is None:
            ws = Workspace(installation_id=inst_id, name=account["login"],
                           github_account_id=str(account["id"]), github_account_login=account["login"],
                           github_account_type=account.get("type", "User"))
            db.add(ws)
            db.flush()
        try:
            allowed = _has_access(gh, inst, user)
        except GitHubError as exc:
            warnings.append(f"{account['login']}: could not verify organization membership ({exc})")
            allowed = False
        member = db.scalar(select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == ws.id, WorkspaceMember.user_id == user.id))
        if allowed:
            if member is None:
                owner = account.get("type") != "Organization"
                db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, github_login=user.github_login,
                                       role="owner" if owner else "member"))
            else:
                member.verified_at = utc_now()
            mine.append(ws)
        elif member is not None:
            db.delete(member)  # left the org / lost access -> revoke
    db.commit()
    return mine, warnings


def resolve_workspace(db: Session, user: CurrentUser, workspace_id: str | None) -> Workspace | None:
    """The workspace for this request: the one asked for, or the user's only one."""
    stmt = (select(Workspace).join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .where(WorkspaceMember.user_id == user.id))
    if workspace_id:
        return db.scalar(stmt.where(Workspace.id == workspace_id))
    rows = db.scalars(stmt.limit(2)).all()
    return rows[0] if len(rows) == 1 else None
