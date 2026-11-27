from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.lifecycle import LifecycleStatus
from app.models.base import new_id, utc_now


class Flag(Base):
    __tablename__ = "flags"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(1000))

    # Link to the flag in Flagsmith (source of truth for flag configuration)
    flagsmith_id: Mapped[str | None] = mapped_column(String(255), index=True)
    integration_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("integrations.id", ondelete="SET NULL"), index=True
    )
    project_name: Mapped[str | None] = mapped_column(String(255))
    environment: Mapped[str | None] = mapped_column(String(255))

    owner: Mapped[str | None] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    lifecycle_status: Mapped[str] = mapped_column(
        String(50), default=LifecycleStatus.CREATED.value, index=True
    )
    lifecycle_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
