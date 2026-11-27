import os

from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings

# Works with any Postgres (currently Supabase).
# On serverless (Vercel) every instance would hold its own pool and exhaust the
# Supabase pooler's client limit, so open a connection per request there instead.
_pool_args = (
    {"poolclass": NullPool}
    if os.environ.get("VERCEL")
    else {"pool_pre_ping": True, "pool_recycle": 300}  # cloud poolers close idle conns
)

engine = create_engine(
    settings.database_url,
    **_pool_args,
    # Cloud poolers (Supabase Supavisor, PgBouncer) in transaction mode don't
    # support server-side prepared statements; disable psycopg's auto-prepare.
    connect_args={"prepare_threshold": None},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
