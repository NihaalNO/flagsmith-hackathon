from datetime import datetime

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import new_id, utc_now


class Integration(Base):
    """A connection to an external system (Flagsmith, GitHub).

    `config` holds NON-secret settings (base URL, project id, environment key).
    `encrypted_secret` holds the API token, Fernet-encrypted with ENCRYPTION_KEY.
    It is never returned by the API.
    """

    __tablename__ = "integrations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(50), index=True)  # "flagsmith" | "github"
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(50), default="pending")  # pending|connected|error
    status_detail: Mapped[str | None] = mapped_column(String(1000))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    encrypted_secret: Mapped[str | None] = mapped_column(Text)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    @property
    def has_secret(self) -> bool:
        return bool(self.encrypted_secret)
