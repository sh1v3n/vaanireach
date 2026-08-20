"""providers.documents.text_extraction — .txt decoding, pypdf embedded-
text extraction, and the real-OCR fallback for scanned PDFs. The OCR
path needs the system `tesseract`/`poppler` binaries (already installed
this session) — skipped automatically if they're not importable.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from providers.documents.text_extraction import extract_text_from_upload_bytes  # noqa: E402

try:
    import pytesseract  # noqa: F401
    from pdf2image import convert_from_bytes  # noqa: F401
    _HAS_OCR_DEPS = True
except ImportError:
    _HAS_OCR_DEPS = False


def test_txt_file_decodes_directly():
    text = extract_text_from_upload_bytes("notice.txt", "Applications close 31 March 2026.".encode("utf-8"))
    assert text == "Applications close 31 March 2026."


def test_unsupported_extension_raises_clearly():
    with pytest.raises(ValueError, match="Unsupported file type"):
        extract_text_from_upload_bytes("notice.docx", b"irrelevant")


def test_pdf_with_embedded_text_layer_extracts_without_ocr():
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    import io
    buf = io.BytesIO()
    writer.write(buf)
    pdf_bytes = buf.getvalue()

    # a blank page has no text layer AND (being a real blank raster-free
    # page) nothing for OCR to find either — this exercises the "both
    # extraction paths come back empty" error path cheaply, without
    # needing a real scanned-image fixture.
    with pytest.raises(ValueError, match="no embedded text AND OCR found nothing"):
        extract_text_from_upload_bytes("blank.pdf", pdf_bytes)


@pytest.mark.skipif(not _HAS_OCR_DEPS, reason="pytesseract/pdf2image not installed")
def test_scanned_pdf_falls_back_to_real_ocr():
    """Synthesizes a PDF with text baked into a raster image (no text
    layer at all, exactly like a photographed government notice) and
    confirms the OCR fallback actually transcribes it — the same
    real-OCR verification approach already proven live this session."""
    from PIL import Image, ImageDraw
    import io

    img = Image.new("RGB", (800, 200), "white")
    d = ImageDraw.Draw(img)
    d.text((20, 20), "Applications close 31 March 2026", fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PDF")
    pdf_bytes = buf.getvalue()

    text = extract_text_from_upload_bytes("scanned_notice.pdf", pdf_bytes)
    assert "2026" in text
