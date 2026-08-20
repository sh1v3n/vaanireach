"""Claim — a single generated statement (in a script or translation) that
must be traceable to one or more SourceFacts and is the unit verification
operates on."""
from __future__ import annotations

from pydantic import Field

from core.models.base import IdentifiedModel
from core.models.enums import Criticality, LanguageCode


class Claim(IdentifiedModel):
    project_id: str
    script_id: str | None = None
    translation_id: str | None = None
    claim_text: str
    language: LanguageCode
    source_fact_ids: list[str] = Field(default_factory=list)
    claim_type: str
    """Free-form label, e.g. "amount", "eligibility", "deadline"."""
    criticality: Criticality
