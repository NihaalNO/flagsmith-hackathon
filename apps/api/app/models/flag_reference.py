from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import new_id, utc_now


class FlagReference(Base):
    """One place in a repository's code where a flag's name appears."""

    __tablename__ = "flag_references"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    flag_id: Mapped[str] = mapped_column(String(36), ForeignKey("flags.id", ondelete="CASCADE"), index=True)
    repository_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(String(1000))
    line: Mapped[int] = mapped_column(Integer)
    snippet: Mapped[str] = mapped_column(String(500))
    commit_sha: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
