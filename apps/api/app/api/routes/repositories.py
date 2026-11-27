from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.integration import Integration
from app.models.repository import Repository
from app.schemas.repository import (
    RepositoryCreate,
    RepositoryProjectLink,
    RepositoryProjectLinkResult,
    RepositoryResponse,
)
from app.services import audit
from app.services.flagsmith import ClientFactory, FlagsmithError, get_flagsmith_client_factory
from app.services.flagsmith_sync import sync_flagsmith
from app.services.projects import flagsmith_integration_for_projects

router = APIRouter(prefix="/repositories", tags=["Repositories"])


@router.get("", response_model=list[RepositoryResponse])
def list_repositories(db: Session = Depends(get_db)):
    return db.scalars(select(Repository).order_by(Repository.full_name)).all()


@router.get("/{repo_id}", response_model=RepositoryResponse)
def get_repository(repo_id: str, db: Session = Depends(get_db)):
    repo = db.get(Repository, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    return repo


@router.post("", response_model=RepositoryResponse, status_code=201)
def create_repository(payload: RepositoryCreate, db: Session = Depends(get_db)):
    if db.scalar(select(Repository).where(Repository.full_name == payload.full_name)):
        raise HTTPException(status_code=409, detail="Repository already registered")
    repo = Repository(
        full_name=payload.full_name,
        name=payload.full_name.split("/", 1)[1],
        default_branch=payload.default_branch,
        github_repo_id=payload.github_repo_id,
    )
    db.add(repo)
    db.flush()
    audit.record(db, "repository.created", "repository", repo.id, {"full_name": repo.full_name})
    db.commit()
    db.refresh(repo)
    return repo


@router.delete("/{repo_id}", status_code=204)
def delete_repository(repo_id: str, db: Session = Depends(get_db)):
    repo = db.get(Repository, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    audit.record(db, "repository.deleted", "repository", repo.id, {"full_name": repo.full_name})
    db.delete(repo)
    db.commit()


@router.put("/{repo_id}/flagsmith-project", response_model=RepositoryProjectLinkResult)
def link_flagsmith_project(
    repo_id: str,
    payload: RepositoryProjectLink,
    db: Session = Depends(get_db),
    make_client: ClientFactory = Depends(get_flagsmith_client_factory),
):
    """Link the repository to an existing Flagsmith project (the one holding its flags), then
    sync that project so its flags are matched by scans and analyses."""
    repo = db.get(Repository, repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    integration_id = payload.integration_id or repo.flagsmith_integration_id
    integration = db.get(Integration, integration_id) if integration_id else flagsmith_integration_for_projects(db)
    if integration is None or integration.provider != "flagsmith" or not integration.encrypted_secret:
        raise HTTPException(status_code=400, detail="No Flagsmith integration with an API key; add one on Connections")
    try:
        with make_client(integration) as client:
            projects = {int(p["id"]): p.get("name") or str(p["id"]) for p in client.list_projects()}
    except FlagsmithError as exc:
        raise HTTPException(status_code=502, detail=f"Flagsmith: {exc}")
    name = projects.get(payload.flagsmith_project_id)
    if name is None:
        raise HTTPException(status_code=404,
                            detail=f"Flagsmith project {payload.flagsmith_project_id} not found for this API key")

    previous = repo.flagsmith_project_id
    repo.flagsmith_project_id = payload.flagsmith_project_id
    repo.flagsmith_integration_id = integration.id
    audit.record(db, "repository.flagsmith_project_linked", "repository", repo.id,
                 {"from": previous, "to": payload.flagsmith_project_id, "project": name})
    db.commit()

    sync, sync_error = None, None
    try:
        with make_client(integration) as client:
            sync = sync_flagsmith(db, integration, client)
        db.commit()
    except FlagsmithError as exc:
        db.rollback()  # the link itself is already saved
        sync_error = f"Linked, but the Flagsmith sync failed: {exc}"
    db.refresh(repo)
    return {"repository": repo, "flagsmith_project_name": name, "sync": sync, "sync_error": sync_error}
