"""Script — narration generated FROM the Source Fact Ledger. The script
generator must never become the source of truth: every claim it produces
must cite the source_fact_ids it is grounded in (see Claim)."""
from __future__ import annotations

from pydantic import Field

from core.models.base import IdentifiedModel
from core.models.enums import LanguageCode, ScriptStatus


class Script(IdentifiedModel):
    project_id: str
    language: LanguageCode
    audience: str
    target_duration_seconds: int
    narration_text: str
    scene_ids: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    source_fact_ids: list[str] = Field(default_factory=list)
    generator_name: str
    version: int = 1
    status: ScriptStatus = ScriptStatus.DRAFT
