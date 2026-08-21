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
    qualifier: str | None = None
    """A short bare phrase (no leading preposition) distinguishing this
    fact from OTHER facts of the same fact_type in the same document —
    e.g. "students filing online applications" vs "institutions
    verifying applications" when a document lists multiple deadlines.
    Set only when needed for disambiguation; None for the common case of
    one fact per type. Exists so narration generation (see
    providers/narrative/template_story_director.py) can voice each fact
    as its own clearly-attributed sentence instead of joining same-typed
    facts into one run-on line that loses which value belongs to what —
    the exact failure mode with tabular/list source data (e.g. a table
    of several closing dates for different applicant categories)."""
    source_span: SourceSpan
    criticality: Criticality
    confidence: float = Field(ge=0.0, le=1.0)
    """Extractor confidence, 0.0-1.0."""
    extractor_name: str
