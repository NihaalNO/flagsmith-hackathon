from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import encrypt_secret
from app.models.integration import Integration
from app.schemas.integration import (
    ConnectionTestResult,
    FlagsmithEnvironment,
    FlagsmithProject,
    IntegrationCreate,
    IntegrationResponse,
    IntegrationUpdate,
    SyncResult,
)
from app.services import audit
from app.services.flagsmith import ClientFactory, FlagsmithError, get_flagsmith_client_factory
from app.services.flagsmith_sync import sync_flagsmith

router = APIRouter(prefix="/integrations", tags=["Integrations"])


def _get_or_404(db: Session, integration_id: str) -> Integration:
    integration = db.get(Integration, integration_id)
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")
    return integration


def _get_flagsmith(db: Session, integration_id: str) -> Integration:
    integration = _get_or_404(db, integration_id)
    if integration.provider != "flagsmith":
        raise HTTPException(status_code=400, detail="Not a Flagsmith integration")
    return integration


def _mark_error(db: Session, integration: Integration, exc: FlagsmithError) -> HTTPException:
    integration.status, integration.status_detail = "error", str(exc)[:1000]
    audit.record(db, "integration.error", "integration", integration.id, {"error": str(exc)[:1000]})
    db.commit()
    return HTTPException(status_code=502, detail=str(exc))


@router.get("", response_model=list[IntegrationResponse])
def list_integrations(db: Session = Depends(get_db)):
    return db.scalars(select(Integration).order_by(Integration.created_at.desc())).all()


@router.get("/{integration_id}", response_model=IntegrationResponse)
def get_integration(integration_id: str, db: Session = Depends(get_db)):
    return _get_or_404(db, integration_id)


@router.post("", response_model=IntegrationResponse, status_code=201)
def create_integration(payload: IntegrationCreate, db: Session = Depends(get_db)):
    integration = Integration(
        provider=payload.provider.value,
        name=payload.name,
        config=payload.config.model_dump(exclude_none=True),
        encrypted_secret=encrypt_secret(payload.api_token) if payload.api_token else None,
    )
    db.add(integration)
    db.flush()
    audit.record(db, "integration.created", "integration", integration.id,
                 {"provider": integration.provider, "name": integration.name,
                  "has_secret": integration.has_secret})
    db.commit()
    db.refresh(integration)
    return integration


@router.patch("/{integration_id}", response_model=IntegrationResponse)
def update_integration(integration_id: str, payload: IntegrationUpdate, db: Session = Depends(get_db)):
    integration = _get_or_404(db, integration_id)
    changed = []
    if payload.name is not None:
        integration.name = payload.name
        changed.append("name")
    if payload.config is not None:
        integration.config = payload.config.model_dump(exclude_none=True)
        changed.append("config")
    if payload.api_token is not None:
        integration.encrypted_secret = encrypt_secret(payload.api_token)
        integration.status, integration.status_detail = "pending", None
        changed.append("api_token")
    if changed:
        audit.record(db, "integration.updated", "integration", integration.id, {"fields": changed})
    db.commit()
    db.refresh(integration)
    return integration


@router.delete("/{integration_id}", status_code=204)
def delete_integration(integration_id: str, db: Session = Depends(get_db)):
    integration = _get_or_404(db, integration_id)
    audit.record(db, "integration.deleted", "integration", integration.id,
                 {"provider": integration.provider})
    db.delete(integration)
    db.commit()


# --- Flagsmith ------------------------------------------------------------

@router.post("/{integration_id}/test", response_model=ConnectionTestResult)
def test_connection(
    integration_id: str,
    db: Session = Depends(get_db),
    make_client: ClientFactory = Depends(get_flagsmith_client_factory),
):
    """Check the stored API key against Flagsmith and update the integration status."""
    integration = _get_flagsmith(db, integration_id)
    try:
        with make_client(integration) as client:
            orgs = client.list_organisations()
    except FlagsmithError as exc:
        integration.status, integration.status_detail = "error", str(exc)[:1000]
        db.commit()
        return ConnectionTestResult(ok=False, detail=str(exc))
    integration.status, integration.status_detail = "connected", None
    audit.record(db, "integration.connected", "integration", integration.id, {})
    db.commit()
    return ConnectionTestResult(ok=True, detail="Connected", organisations=[o.get("name", "") for o in orgs])


@router.get("/{integration_id}/flagsmith/projects", response_model=list[FlagsmithProject])
def flagsmith_projects(
    integration_id: str,
    db: Session = Depends(get_db),
    make_client: ClientFactory = Depends(get_flagsmith_client_factory),
):
    integration = _get_flagsmith(db, integration_id)
    try:
        with make_client(integration) as client:
            return client.list_projects()
    except FlagsmithError as exc:
        raise _mark_error(db, integration, exc)


@router.get("/{integration_id}/flagsmith/projects/{project_id}/environments",
            response_model=list[FlagsmithEnvironment])
def flagsmith_environments(
    integration_id: str,
    project_id: int,
    db: Session = Depends(get_db),
    make_client: ClientFactory = Depends(get_flagsmith_client_factory),
):
    integration = _get_flagsmith(db, integration_id)
    try:
        with make_client(integration) as client:
            return client.list_environments(project_id)
    except FlagsmithError as exc:
        raise _mark_error(db, integration, exc)


@router.post("/{integration_id}/sync", response_model=SyncResult)
def sync_integration(
    integration_id: str,
    db: Session = Depends(get_db),
    make_client: ClientFactory = Depends(get_flagsmith_client_factory),
):
    """Import/update flags from the configured Flagsmith project (and environment state)."""
    integration = _get_flagsmith(db, integration_id)
    try:
        with make_client(integration) as client:
            stats = sync_flagsmith(db, integration, client)
    except FlagsmithError as exc:
        db.rollback()
        integration = _get_flagsmith(db, integration_id)
        raise _mark_error(db, integration, exc)
    db.commit()
    return stats
