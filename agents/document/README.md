# Document Agent

Selects and runs the right `DocumentParser` for an uploaded file (PDF,
DOCX, image) — genuinely needs a decision (which parser, whether OCR is
required) rather than a fixed pipeline step.

Will implement/consume: [`core.interfaces.document_parser.DocumentParser`](../../core/interfaces/document_parser.py)

No logic implemented in Phase 0 — this package only reserves the import
namespace for Phase 1.
