from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import new_id, utc_now


class Workspace(Base):
    """One per GitHub account (user or organization) the GitHub App is installed on.

    Everything (flags, repos, integrations, audit log) belongs to exactly one workspace.
    """

    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255))
    installation_id: Mapped[str] = mapped_column(String(50), unique=True)
    github_account_id: Mapped[str] = mapped_column(String(50), index=True)
    github_account_login: Mapped[str] = mapped_column(String(255))
    github_account_type: Mapped[str] = mapped_column(String(20))  # "User" | "Organization"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class WorkspaceMember(Base):
    """A signed-in user with access to a workspace (verified against GitHub)."""

    __tablename__ = "workspace_members"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", name="uq_workspace_member"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)  # Supabase user id
    github_login: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="member")  # "owner" | "member"
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
