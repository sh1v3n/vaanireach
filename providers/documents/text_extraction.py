"""text_extraction — shared document -> plain-text extraction, used by
both dashboard/local_demo.py (Streamlit) and backend/app/routes/pipeline.py
(FastAPI). Pulled out of local_demo.py so there is exactly one OCR
implementation, not two copies that can drift.

.txt is decoded directly. .pdf tries pypdf's embedded text layer first
(fast, no image processing) — if that finds nothing (a scanned/
photographed PDF with no real text layer), falls back to real OCR:
pdf2image renders each page to an image (needs the system `poppler` —
`pdftoppm`), pytesseract (needs the system `tesseract` binary)
transcribes each page. English-only by default — see requirements.txt
for installing Hindi/other Indic-script language data.
"""
from __future__ import annotations

from io import BytesIO


def ocr_pdf_bytes(pdf_bytes: bytes) -> str:
    """Real OCR fallback for scanned/image-only PDFs. Slower than
    pypdf's text-layer read (real image processing per page) — only
    reached when that read finds nothing."""
    import pytesseract
    from pdf2image import convert_from_bytes

    pages = convert_from_bytes(pdf_bytes)
    page_texts = [pytesseract.image_to_string(page) for page in pages]
    return "\n".join(page_texts)


def extract_text_from_upload_bytes(filename: str, file_bytes: bytes) -> str:
    """filename is used only for its extension and in error messages —
    the caller (Streamlit's UploadedFile, or FastAPI's UploadFile) has
    already read the file into `file_bytes`."""
    name = filename.lower()
    if name.endswith(".txt"):
        return file_bytes.decode("utf-8", errors="replace")
    if name.endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(file_bytes))
        pages_text = [page.extract_text() or "" for page in reader.pages]
        text = "\n".join(pages_text)

        if text.strip():
            return text

        ocr_text = ocr_pdf_bytes(file_bytes)
        if not ocr_text.strip():
            raise ValueError(
                f"'{filename}' ({len(reader.pages)} page(s)) has no embedded text AND OCR found "
                "nothing readable either — the scan may be too low-quality, blank, or in a script the "
                "installed Tesseract language data doesn't cover (English-only by default; see "
                "requirements.txt for adding Hindi/other Indic scripts). Try pasting the text directly."
            )
        return ocr_text
    raise ValueError(f"Unsupported file type: {filename} — upload a .txt or .pdf")
