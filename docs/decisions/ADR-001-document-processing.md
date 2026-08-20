# ADR-001: Document Processing Hierarchy

**Status:** Accepted (interface). Implementation deferred to Phase 1.

## Context

VaaniReach must ingest official documents that are not always clean,
digital-native PDFs — they may be DOCX, scanned images, or PDFs requiring
OCR. Every downstream fact must be traceable back to exactly where in the
source it came from.

## Decision

Define a `document → page → section/paragraph → source span` hierarchy
now (`core/models/document.py`, `core/provenance/models.py`), and a
`DocumentParser` interface (`core/interfaces/document_parser.py`) with:

```python
supports(mime_type) -> bool
requires_ocr(file_path) -> bool
parse(file_path, document_id) -> list[DocumentPage]
extract_tables(file_path) -> list[dict]
```

No concrete parser (pdfplumber, python-docx, pytesseract, or otherwise) is
selected or implemented in Phase 0.

## Consequences

- The Document Agent can select a parser per MIME type at runtime once
  concrete parsers exist, without changing anything downstream.
- `SourceFact.source_span` can always resolve to a `DocumentPage` and a
  precise text span, regardless of which parser produced it.
