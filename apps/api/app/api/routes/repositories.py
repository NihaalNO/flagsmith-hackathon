from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.repository import Repository
from app.schemas.repository import RepositoryCreate, RepositoryResponse
from app.services import audit

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
