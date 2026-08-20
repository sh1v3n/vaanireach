"""Shared base classes for all VaaniReach domain models.

These are plain Pydantic models — framework-agnostic, importable by the
backend, agents, tests, or a future CLI without pulling in FastAPI/SQLModel.
Persistence (SQLModel table classes) is a Phase 1 concern layered on top of
these, not a replacement for them.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field


def _new_id() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class VaaniBaseModel(BaseModel):
    """Base for every VaaniReach domain model.

    - `extra="forbid"` catches typos/drift between producers and consumers
      early, which matters a lot when multiple pipeline stages hand a model
      to each other.
    - `validate_assignment=True` keeps that same guarantee for in-place
      mutation, not just construction.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class IdentifiedModel(VaaniBaseModel):
    """Base for models that are persisted / referenced by id.

    `schema_version` exists so we can evolve individual model shapes later
    (e.g. add a field, change a type) without a big-bang migration — a
    consumer can branch on `schema_version` if it ever needs to. Start at 1;
    bump only on breaking changes.
    """

    id: str = Field(default_factory=_new_id)
    schema_version: int = 1
    created_at: datetime = Field(default_factory=_utcnow)
