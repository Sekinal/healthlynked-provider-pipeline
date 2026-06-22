"""Engine + session helpers. DATABASE_URL drives sqlite (dev) vs postgres (prod)."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlmodel import Session, SQLModel, create_engine

from pipeline.config import settings

# Normalize postgres URL for psycopg v3 driver if a bare scheme is given.
_url = settings.database_url
if _url.startswith("postgres://"):
    _url = _url.replace("postgres://", "postgresql+psycopg://", 1)
elif _url.startswith("postgresql://"):
    _url = _url.replace("postgresql://", "postgresql+psycopg://", 1)

_connect_args = {"check_same_thread": False} if _url.startswith("sqlite") else {}
engine = create_engine(_url, connect_args=_connect_args)


def init_db() -> None:
    """Create tables. Alembic owns migrations in prod; this is for dev/tests."""
    # Import models so they register on SQLModel.metadata before create_all.
    import db.models  # noqa: F401

    SQLModel.metadata.create_all(engine)


@contextmanager
def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
