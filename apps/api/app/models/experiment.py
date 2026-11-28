from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.tenancy import TenantScoped
from app.models.base import new_id, utc_now


class Experiment(TenantScoped, Base):
    """An A/B test on a flag's variants. FlagPilot reads the split from Flagsmith but never
    changes it; results come from exposure/conversion events the app sends."""

    __tablename__ = "experiments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    flag_id: Mapped[str] = mapped_column(String(36), ForeignKey("flags.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    hypothesis: Mapped[str | None] = mapped_column(Text)
    primary_metric: Mapped[str] = mapped_column(String(100))
    environment_name: Mapped[str | None] = mapped_column(String(255))
    # [{"key", "name", "is_control", "expected_share", "flagsmith_option_id"}]
    variants: Mapped[list] = mapped_column(JSON, default=list)
    minimum_detectable_effect: Mapped[float] = mapped_column(Float, default=0.1)
    alpha: Mapped[float] = mapped_column(Float, default=0.05)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)  # draft|running|stopped|concluded
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    concluded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    winner: Mapped[str | None] = mapped_column(String(100))
    conclusion_note: Mapped[str | None] = mapped_column(Text)
    # sha256 of the event-ingestion key; the key itself is shown once and never stored.
    ingest_key_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ExperimentSubject(TenantScoped, Base):
    """One subject (end user) in an experiment: the variant they were first exposed to and
    whether they converted. Only a salted hash of the subject id is stored."""

    __tablename__ = "experiment_subjects"
    __table_args__ = (UniqueConstraint("experiment_id", "subject_hash", name="uq_experiment_subject"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    experiment_id: Mapped[str] = mapped_column(String(36), ForeignKey("experiments.id", ondelete="CASCADE"), index=True)
    subject_hash: Mapped[str] = mapped_column(String(64))
    variant: Mapped[str] = mapped_column(String(100))
    exposed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    converted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
