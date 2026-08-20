"""local_demo — upload a document, get a real video out. The fastest
local "does this actually work end to end" loop for the template
pipeline (rendering/multilingual_video.run_full_pipeline): real Groq
fact extraction, real TemplateStoryDirector narrative planning, real
fact-aware B-roll images, real translation/TTS, real avatar lip-sync,
real captions, real composition — no hardcoded fixtures anywhere in
this path.

Deliberately separate from dashboard/app.py, which drives a different,
older pipeline (Gemini-drafted scripts, HuggingFace visuals, MoviePy
renderer) — this file only ever imports from rendering/multilingual_video.py
and its own dependencies, never from app.py or vice versa.

Run: PYTHONPATH=. .venv/bin/streamlit run dashboard/local_demo.py
"""
from __future__ import annotations

import logging
import uuid

import streamlit as st
from dotenv import load_dotenv

load_dotenv(".env")
logging.basicConfig(level=logging.INFO)

from core.models.enums import LanguageCode  # noqa: E402
from rendering.multilingual_video import run_full_pipeline  # noqa: E402

st.set_page_config(page_title="VaaniReach — Local Demo", page_icon="🎬", layout="centered")

LANGUAGE_LABELS: dict[LanguageCode, str] = {
    LanguageCode.EN: "English",
    LanguageCode.HI: "हिन्दी (Hindi)",
    LanguageCode.MR: "मराठी (Marathi)",
    LanguageCode.BN: "বাংলা (Bengali)",
    LanguageCode.TA: "தமிழ் (Tamil)",
    LanguageCode.TE: "తెలుగు (Telugu)",
    LanguageCode.KN: "ಕನ್ನಡ (Kannada)",
    LanguageCode.ML: "മലയാളം (Malayalam)",
    LanguageCode.GU: "ગુજરાતી (Gujarati)",
}


def _ocr_pdf_bytes(pdf_bytes: bytes) -> str:
    """Real OCR fallback for scanned/image-only PDFs: renders each page
    to an image (pdf2image, needs the system `poppler` — `pdftoppm`) then
    runs Tesseract (pytesseract, needs the system `tesseract` binary) on
    each page image. Slower than pypdf's text-layer read (real image
    processing per page), only reached when that read finds nothing.

    English-only by default (the base `tesseract` install ships English
    OCR data only) — see requirements.txt for installing Hindi/other
    Indic-script language data if you need to OCR non-English scans."""
    import pytesseract
    from pdf2image import convert_from_bytes

    pages = convert_from_bytes(pdf_bytes)
    page_texts = [pytesseract.image_to_string(page) for page in pages]
    return "\n".join(page_texts)


def extract_text_from_upload(uploaded_file) -> str:
    """.txt is read directly. .pdf tries pypdf's embedded text layer
    first (fast, no image processing) — if that finds nothing (a
    scanned/image-only PDF with no real text layer), falls back to real
    OCR (_ocr_pdf_bytes). Anything else is rejected with a clear error
    rather than silently mis-decoding bytes as text."""
    name = uploaded_file.name.lower()
    if name.endswith(".txt"):
        return uploaded_file.read().decode("utf-8", errors="replace")
    if name.endswith(".pdf"):
        from pypdf import PdfReader
        from io import BytesIO

        pdf_bytes = uploaded_file.getvalue()
        reader = PdfReader(BytesIO(pdf_bytes))
        pages_text = [page.extract_text() or "" for page in reader.pages]
        text = "\n".join(pages_text)

        if text.strip():
            return text

        st.info(f"'{uploaded_file.name}' has no embedded text layer — running OCR (this takes a few seconds)…")
        ocr_text = _ocr_pdf_bytes(pdf_bytes)
        if not ocr_text.strip():
            raise ValueError(
                f"'{uploaded_file.name}' ({len(reader.pages)} page(s)) has no embedded text AND OCR found "
                "nothing readable either — the scan may be too low-quality, blank, or in a script the "
                "installed Tesseract language data doesn't cover (English-only by default; see "
                "requirements.txt for adding Hindi/other Indic scripts). Try pasting the text directly."
            )
        return ocr_text
    raise ValueError(f"Unsupported file type: {uploaded_file.name} — upload a .txt or .pdf")


st.title("🎬 VaaniReach — Local Demo")
st.caption(
    "Upload a government notice (.txt or .pdf) → real fact extraction → real multilingual video, "
    "end to end. Same pipeline as tests/demo_multilingual_video.py, just with a real document instead "
    "of a hardcoded fixture."
)

uploaded_file = st.file_uploader("Government notice", type=["txt", "pdf"])
pasted_text = st.text_area(
    "...or paste the notice text directly", height=150,
    placeholder="Paste raw notice text here if you don't have a file handy.",
)

selected_languages = st.multiselect(
    "Languages to generate", options=list(LANGUAGE_LABELS.keys()),
    default=[LanguageCode.EN], format_func=lambda lc: LANGUAGE_LABELS[lc],
)

generate_clicked = st.button("Generate video(s)", type="primary")

if generate_clicked:
    document_text = ""
    if uploaded_file is not None:
        try:
            document_text = extract_text_from_upload(uploaded_file)
        except ValueError as exc:
            st.error(str(exc))
            st.stop()
    elif pasted_text.strip():
        document_text = pasted_text.strip()

    if not document_text.strip():
        st.error("Upload a file or paste some text first.")
        st.stop()
    if not selected_languages:
        st.error("Pick at least one language.")
        st.stop()

    with st.expander("Extracted document text (first 2000 chars)", expanded=False):
        st.text(document_text[:2000])

    project_id = f"local-demo-{uuid.uuid4().hex[:8]}"

    with st.status("Running the pipeline…", expanded=True) as status:
        status.write("🔎 Extracting facts and generating video(s) — this calls real Groq/Cloudflare/Sarvam/Hedra/D-ID APIs, may take a few minutes per language…")
        try:
            results = run_full_pipeline(document_text, languages=selected_languages, project_id=project_id)
        except ValueError as exc:
            status.update(label="Failed — no facts extracted", state="error")
            st.error(str(exc))
            st.stop()
        except Exception as exc:  # noqa: BLE001 - surface any pipeline failure to the user, not a crash
            status.update(label="Failed", state="error")
            st.exception(exc)
            st.stop()
        status.update(label="Done.", state="complete")

    for result in results:
        st.subheader(LANGUAGE_LABELS.get(result.language, result.language.value))
        col1, col2, col3 = st.columns(3)
        col1.metric("Facts verified", f"{result.verified_count}/{len(result.scenes)}")
        col2.metric("Blocking issues", result.blocking_count)
        avatar_label = (
            "✅ real" if result.avatar_tier in (1, 2)
            else "⚠️ placeholder (no lip-sync)" if result.avatar_tier == 3
            else "⚠️ degraded (plain B-roll)"
        )
        col3.metric("Avatar", avatar_label)

        if result.video_asset.storage_path_mp4:
            st.video(result.video_asset.storage_path_mp4)
        else:
            st.warning("No video file produced for this language.")

        with st.expander("Scene-by-scene narration"):
            for scene in result.scenes:
                st.markdown(f"**[{scene.narrative_role.value}]** {scene.narration_segment_text}")
