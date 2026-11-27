from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.tenancy import TenantScoped
from app.models.base import new_id, utc_now


class FlagEnvironmentState(TenantScoped, Base):
    """A flag's live state in one Flagsmith environment, as of the last sync.

    Lets the lifecycle engine tell a uniform 100% production rollout from a partial one
    (segment/identity overrides, multivariate splits) and judge configuration stability.
    """

    __tablename__ = "flag_environment_states"
    __table_args__ = (UniqueConstraint("flag_id", "flagsmith_environment_id", name="uq_flag_env_state"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    flag_id: Mapped[str] = mapped_column(String(36), ForeignKey("flags.id", ondelete="CASCADE"), index=True)
    flagsmith_environment_id: Mapped[int] = mapped_column(Integer)
    environment_name: Mapped[str] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    value: Mapped[object | None] = mapped_column(JSON)
    segment_overrides: Mapped[int] = mapped_column(Integer, default=0)
    # None when Flagsmith doesn't report it (Edge-enabled projects).
    identity_overrides: Mapped[int | None] = mapped_column(Integer)
    # Smallest non-zero multivariate allocation when users are split across values; 100 when
    # one variant gets everyone; None when there is no split.
    percentage_allocation: Mapped[float | None] = mapped_column(Float)
    last_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
