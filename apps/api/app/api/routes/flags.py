from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.lifecycle import ALLOWED_TRANSITIONS, LifecycleStatus, can_transition
from app.models.base import utc_now
from app.models.flag import Flag
from app.models.flag_reference import FlagReference
from app.models.repository import Repository
from app.models.integration import Integration
from app.schemas.github import FlagReferenceResponse
from app.schemas.flag import FlagCreate, FlagResponse, FlagUpdate, LifecycleTransition
from app.services import audit
from app.services.flagsmith import ClientFactory, FlagsmithError, get_flagsmith_client_factory

router = APIRouter(prefix="/flags", tags=["Flags"])


def _get_or_404(db: Session, flag_id: str) -> Flag:
    flag = db.get(Flag, flag_id)
    if not flag:
        raise HTTPException(status_code=404, detail="Flag not found")
    return flag


@router.get("", response_model=list[FlagResponse])
def list_flags(
    status: LifecycleStatus | None = Query(default=None, description="Filter by lifecycle status"),
    db: Session = Depends(get_db),
):
    stmt = select(Flag).order_by(Flag.created_at.desc())
    if status:
        stmt = stmt.where(Flag.lifecycle_status == status.value)
    return db.scalars(stmt).all()


@router.get("/lifecycle/transitions", response_model=dict[LifecycleStatus, list[LifecycleStatus]])
def lifecycle_transitions():
    """Allowed lifecycle moves, so the frontend can show only valid options."""
    return {k: sorted(v) for k, v in ALLOWED_TRANSITIONS.items()}


@router.get("/{flag_id}", response_model=FlagResponse)
def get_flag(flag_id: str, db: Session = Depends(get_db)):
    return _get_or_404(db, flag_id)


@router.post("", response_model=FlagResponse, status_code=201)
def create_flag(
    payload: FlagCreate,
    db: Session = Depends(get_db),
    make_client: ClientFactory = Depends(get_flagsmith_client_factory),
):
    data = payload.model_dump(exclude={"repository_id"})
    integration, project_id = None, None
    if payload.repository_id:
        repo = db.get(Repository, payload.repository_id)
        if not repo:
            raise HTTPException(status_code=404, detail="Repository not found")
        if repo.flagsmith_project_id is None or not repo.flagsmith_integration_id:
            raise HTTPException(status_code=400, detail="Repository has no Flagsmith project; run POST /api/github/sync")
        integration = db.get(Integration, repo.flagsmith_integration_id)
        project_id = repo.flagsmith_project_id
        data["project_name"] = data.get("project_name") or repo.full_name
    elif payload.integration_id:
        integration = db.get(Integration, payload.integration_id)
        if not integration or integration.provider != "flagsmith":
            raise HTTPException(status_code=404, detail="Flagsmith integration not found")
        project_id = (integration.config or {}).get("project_id")
        if project_id is None:
            raise HTTPException(status_code=400, detail="Integration has no config.project_id")
    if integration is None and (payload.repository_id or payload.integration_id):
        raise HTTPException(status_code=404, detail="Flagsmith integration not found")

    dup = select(Flag).where(Flag.name == payload.name)
    dup = dup.where(Flag.flagsmith_project_id == int(project_id)) if project_id is not None \
        else dup.where(Flag.flagsmith_project_id.is_(None))
    if db.scalar(dup):
        raise HTTPException(status_code=409, detail="A flag with this name already exists in this project")

    if integration is not None:
        # Flagsmith is the source of truth: create there first, store only if that succeeds.
        data["integration_id"] = integration.id
        data["flagsmith_project_id"] = int(project_id)
        try:
            with make_client(integration) as client:
                feature = client.create_feature(
                    int(project_id), payload.name, payload.description, payload.enabled
                )
        except FlagsmithError as exc:
            raise HTTPException(status_code=502, detail=f"Flagsmith: {exc}")
        data["flagsmith_id"] = str(feature["id"])

    flag = Flag(**data)
    db.add(flag)
    db.flush()  # assigns flag.id
    audit.record(db, "flag.created", "flag", flag.id,
                 {"name": flag.name, "flagsmith_id": flag.flagsmith_id})
    db.commit()
    db.refresh(flag)
    return flag


@router.patch("/{flag_id}", response_model=FlagResponse)
def update_flag(flag_id: str, payload: FlagUpdate, db: Session = Depends(get_db)):
    flag = _get_or_404(db, flag_id)
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(flag, field, value)
    if changes:
        audit.record(db, "flag.updated", "flag", flag.id, {"changes": changes})
    db.commit()
    db.refresh(flag)
    return flag


@router.post("/{flag_id}/lifecycle", response_model=FlagResponse)
def transition_lifecycle(flag_id: str, payload: LifecycleTransition, db: Session = Depends(get_db)):
    flag = _get_or_404(db, flag_id)
    current = flag.lifecycle_status
    target = payload.status.value
    if not can_transition(current, target):
        allowed = sorted(ALLOWED_TRANSITIONS[LifecycleStatus(current)])
        raise HTTPException(
            status_code=422,
            detail=f"Cannot move flag from '{current}' to '{target}'. Allowed: {allowed}",
        )
    flag.lifecycle_status = target
    flag.lifecycle_changed_at = utc_now()
    audit.record(
        db, "flag.lifecycle_changed", "flag", flag.id,
        {"from": current, "to": target, "reason": payload.reason},
    )
    db.commit()
    db.refresh(flag)
    return flag


@router.delete("/{flag_id}", status_code=204)
def delete_flag(flag_id: str, db: Session = Depends(get_db)):
    flag = _get_or_404(db, flag_id)
    audit.record(db, "flag.deleted", "flag", flag.id, {"name": flag.name})
    db.delete(flag)
    db.commit()


@router.get("/{flag_id}/references", response_model=list[FlagReferenceResponse])
def flag_references(flag_id: str, db: Session = Depends(get_db)):
    """Where this flag appears in code, from the latest scan of each repository."""
    _get_or_404(db, flag_id)
    rows = db.execute(
        select(FlagReference, Repository.full_name)
        .join(Repository, Repository.id == FlagReference.repository_id)
        .where(FlagReference.flag_id == flag_id)
        .order_by(Repository.full_name, FlagReference.path, FlagReference.line)
    ).all()
    return [
        {**{c: getattr(ref, c) for c in ("id", "repository_id", "path", "line", "snippet",
                                          "commit_sha", "created_at")},
         "repository": full_name,
         "url": f"https://github.com/{full_name}/blob/{ref.commit_sha}/{ref.path}#L{ref.line}"
         if ref.commit_sha else None}
        for ref, full_name in rows
    ]
