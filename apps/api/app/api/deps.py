from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.auth import CurrentUser, get_current_user
from app.core.database import get_db
from app.core.tenancy import current_workspace_id
from app.models.workspace import Workspace
from app.services.workspaces import resolve_workspace


async def get_workspace(
    x_workspace_id: str | None = Header(default=None, description="Workspace to act in (optional if you have only one)"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Workspace:
    # Async on purpose so the ContextVar reaches the endpoint (see core/tenancy.py).
    ws = await run_in_threadpool(resolve_workspace, db, user, x_workspace_id)
    if ws is None:
        if x_workspace_id:
            raise HTTPException(403, "You don't have access to this workspace")
        raise HTTPException(
            409, "Choose a workspace: send X-Workspace-Id (GET /api/workspaces lists yours; "
                 "if empty, install the GitHub App on your account or organization)")
    current_workspace_id.set(ws.id)
    return ws
