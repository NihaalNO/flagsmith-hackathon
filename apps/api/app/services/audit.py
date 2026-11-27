from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog


def record(
    db: Session,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    details: dict | None = None,
    actor: str | None = None,
) -> AuditLog:
    """Add an audit entry to the current transaction (caller commits)."""
    entry = AuditLog(
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        actor=actor,
        details=details or {},
    )
    db.add(entry)
    return entry
