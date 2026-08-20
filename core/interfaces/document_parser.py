"""DocumentParser — the Document Intelligence Layer's contract.

No concrete implementation exists in Phase 0 (no PDF/DOCX/OCR library is
wired up). A future implementation (pdfplumber, python-docx, pytesseract,
etc.) lives in providers/ or agents/document/, not here.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from core.models.document import DocumentPage


class DocumentParser(ABC):
    @abstractmethod
    def supports(self, mime_type: str) -> bool:
        """Whether this parser can handle the given MIME type."""
        raise NotImplementedError("DocumentParser.supports not implemented — Phase 0 interface stub")

    @abstractmethod
    def requires_ocr(self, file_path: str) -> bool:
        """Whether this file needs OCR before text extraction (e.g. a
        scanned/image-only PDF)."""
        raise NotImplementedError("DocumentParser.requires_ocr not implemented — Phase 0 interface stub")

    @abstractmethod
    def parse(self, file_path: str, document_id: str) -> list[DocumentPage]:
        """Extract page-level text, headings, and paragraphs."""
        raise NotImplementedError("DocumentParser.parse not implemented — Phase 0 interface stub")

    @abstractmethod
    def extract_tables(self, file_path: str) -> list[dict[str, Any]]:
        """Extract table structures, if any, as a list of simple dicts."""
        raise NotImplementedError("DocumentParser.extract_tables not implemented — Phase 0 interface stub")
