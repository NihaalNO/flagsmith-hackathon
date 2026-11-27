from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AuditLogResponse(BaseModel):
    id: str
    action: str
    resource_type: str
    resource_id: str | None
    actor: str | None
    details: dict
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
