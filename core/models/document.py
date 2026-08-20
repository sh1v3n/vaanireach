"""Document + DocumentPage — the ingestion layer's output.

Hierarchy per docs/architecture.md: document -> page -> (section /
paragraph, held as text on the page) -> source span. Every SourceFact
traces back to a DocumentPage via a SourceSpan.
"""
from __future__ import annotations

from typing import Any

from pydantic import Field

from core.models.base import IdentifiedModel
from core.models.enums import DocumentType, IngestionStatus


class Document(IdentifiedModel):
    project_id: str
    filename: str
    original_filename: str
    file_type: DocumentType
    mime_type: str
    size_bytes: int
    storage_path: str
    checksum_sha256: str
    page_count: int | None = None
    ingestion_status: IngestionStatus = IngestionStatus.PENDING
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentPage(IdentifiedModel):
    document_id: str
    page_number: int
    raw_text: str
    ocr_applied: bool = False
    heading_texts: list[str] = Field(default_factory=list)
    paragraph_texts: list[str] = Field(default_factory=list)
    table_blocks: list[dict[str, Any]] = Field(default_factory=list)
