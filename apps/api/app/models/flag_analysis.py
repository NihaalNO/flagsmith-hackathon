from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.tenancy import TenantScoped
from app.models.base import new_id, utc_now


class FlagAnalysis(TenantScoped, Base):
    """One lifecycle-engine run for a flag: code analysis, stale finding, lifecycle
    evaluation and cleanup plan. Summary columns are for listing and filtering; the full
    engine output is kept in `result`. Rows are append-only history."""

    __tablename__ = "flag_analyses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    flag_id: Mapped[str] = mapped_column(String(36), ForeignKey("flags.id", ondelete="CASCADE"), index=True)
    engine_version: Mapped[str] = mapped_column(String(20))
    stale_status: Mapped[str] = mapped_column(String(40), index=True)
    stale_confidence: Mapped[float] = mapped_column(Float)
    cleanup_direction: Mapped[str] = mapped_column(String(40))
    suggested_state: Mapped[str] = mapped_column(String(50))
    recommended_action: Mapped[str] = mapped_column(String(50))
    confirmed_references: Mapped[int] = mapped_column(Integer, default=0)
    potential_references: Mapped[int] = mapped_column(Integer, default=0)
    dynamic_references: Mapped[int] = mapped_column(Integer, default=0)
    llm_status: Mapped[str] = mapped_column(String(40))
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
