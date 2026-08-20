"""SQLite engine + session scaffold.

Deliberately minimal for Phase 0: no table classes are defined yet. The
domain model in core/models/ is plain Pydantic and framework-agnostic;
Phase 1 will add a thin SQLModel persistence layer (e.g.
backend/app/db_models.py) once those schemas have stabilized, rather than
coupling core/models to a DB shape prematurely.
"""
from __future__ import annotations

from collections.abc import Generator

from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings

settings = get_settings()

_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=_connect_args)


def create_db_and_tables() -> None:
    """No-op in Phase 0 — no SQLModel table classes exist yet. Kept as the
    call site Phase 1 will fill in (`SQLModel.metadata.create_all(engine)`)."""
    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
