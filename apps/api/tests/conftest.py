import os

from cryptography.fernet import Fernet

# Make tests self-contained (CI has no .env). Real env/.env values take precedence.
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("SUPABASE_URL", "https://test-project.supabase.co")

import pytest  # noqa: E402
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.core.auth import CurrentUser, current_actor, get_current_user
from app.core.database import Base, get_db
from app.main import app

# Tests use an in-memory SQLite DB so they never touch Supabase.
engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)


@pytest.fixture()
def client():
    Base.metadata.create_all(engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    def override_user():
        current_actor.set("tester@example.com")
        return CurrentUser(id="user-1", email="tester@example.com", role="authenticated")

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_user
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)


@pytest.fixture()
def anon_client(client):
    """Same app, but with real token checking (no auth override)."""
    app.dependency_overrides.pop(get_current_user, None)
    return client
