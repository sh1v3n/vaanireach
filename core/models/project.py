"""Project — the top-level container for one outreach-video job: one source
document set, one set of target languages, one pipeline run."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from core.models.base import IdentifiedModel, _utcnow
from core.models.enums import LanguageCode, ProjectStatus


class Project(IdentifiedModel):
    name: str
    description: str | None = None
    target_languages: list[LanguageCode] = Field(default_factory=list)
    status: ProjectStatus = ProjectStatus.DRAFT
    updated_at: datetime = Field(default_factory=_utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)
