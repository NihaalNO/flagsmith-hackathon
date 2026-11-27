from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings

# Works with any Postgres (currently Supabase).
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,  # drop dead connections (cloud poolers close idle ones)
    pool_recycle=300,
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
