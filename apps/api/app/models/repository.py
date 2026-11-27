from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.tenancy import TenantScoped
from app.models.base import new_id, utc_now


class Repository(TenantScoped, Base):
    __tablename__ = "repositories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    github_repo_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(255), unique=True)  # e.g. "acme/web-app"
    default_branch: Mapped[str] = mapped_column(String(100), default="main")
    # GitHub App installation that grants access to this repo (None = added manually).
    installation_id: Mapped[str | None] = mapped_column(String(50), index=True)
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_scan_sha: Mapped[str | None] = mapped_column(String(40))
    # Each repo gets its own Flagsmith project (created on GitHub sync).
    flagsmith_project_id: Mapped[int | None] = mapped_column(Integer, index=True)
    flagsmith_integration_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("integrations.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
