"""SourceFact — the Source Fact Ledger. This is the single source of truth
for the whole pipeline: scripts, translations, and claims are generated
FROM facts and verified AGAINST facts, never the other way around."""
from __future__ import annotations

from pydantic import Field

from core.models.base import IdentifiedModel
from core.models.enums import Criticality, FactType
from core.provenance.models import SourceSpan


class SourceFact(IdentifiedModel):
    project_id: str
    document_id: str
    fact_type: FactType
    value: str
    """Normalized value, e.g. "₹2000" or "2026-03-31"."""
    raw_text: str
    """The literal text the value was extracted from."""
    source_span: SourceSpan
    criticality: Criticality
    confidence: float = Field(ge=0.0, le=1.0)
    """Extractor confidence, 0.0-1.0."""
    extractor_name: str
