"""Provenance data model.

This is what lets the future dashboard answer: "where did this generated
statement come from?" — see docs/data-model.md for the full chain
(Document -> SourceFact -> Claim -> VerificationResult -> ProvenanceLink).
"""
from __future__ import annotations

from core.models.base import IdentifiedModel, VaaniBaseModel
from core.models.enums import VerificationStatus


class SourceSpan(VaaniBaseModel):
    """A precise pointer into a source document. Every SourceFact carries
    exactly one of these — it is the only place a fact is allowed to
    originate from."""

    document_id: str
    page_number: int
    text_span: str
    section_heading: str | None = None
    paragraph_index: int | None = None


class ProvenanceLink(IdentifiedModel):
    """Dashboard-facing join: a generated claim, the source fact(s) it was
    grounded in, and the current verification status. This is the record
    the "fact-level highlighting" UI (Section 6 of the spec) renders."""

    project_id: str
    claim_id: str
    source_fact_id: str
    source_span: SourceSpan
    verification_status: VerificationStatus
