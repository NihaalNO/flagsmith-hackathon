from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.tenancy import TenantScoped
from app.core.lifecycle import LifecycleStatus
from app.models.base import new_id, utc_now


class Flag(TenantScoped, Base):
    __tablename__ = "flags"
    # Names are unique per Flagsmith project (each repo gets its own project).
    __table_args__ = (UniqueConstraint("workspace_id", "flagsmith_project_id", "name", name="uq_flags_project_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), index=True)
    description: Mapped[str | None] = mapped_column(String(1000))

    # Link to the flag in Flagsmith (source of truth for flag configuration)
    flagsmith_id: Mapped[str | None] = mapped_column(String(255), index=True)
    flagsmith_project_id: Mapped[int | None] = mapped_column(Integer, index=True)
    integration_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("integrations.id", ondelete="SET NULL"), index=True
    )
    project_name: Mapped[str | None] = mapped_column(String(255))
    environment: Mapped[str | None] = mapped_column(String(255))

    owner: Mapped[str | None] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    # Feature metadata from Flagsmith (filled by sync; None when unknown).
    flagsmith_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    flagsmith_tags: Mapped[list | None] = mapped_column(JSON)            # tag labels
    flagsmith_owner_ids: Mapped[list | None] = mapped_column(JSON)       # Flagsmith user ids
    flagsmith_type: Mapped[str | None] = mapped_column(String(20))       # STANDARD | MULTIVARIATE
    is_archived_in_flagsmith: Mapped[bool] = mapped_column(Boolean, default=False)
    # Multivariate options: [{"option_id", "value", "default_allocation"}] (None for standard flags).
    flagsmith_variants: Mapped[list | None] = mapped_column(JSON)

    lifecycle_status: Mapped[str] = mapped_column(
        String(50), default=LifecycleStatus.CREATED.value, index=True
    )
    lifecycle_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
