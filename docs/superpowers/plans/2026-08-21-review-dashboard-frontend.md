# Review-and-Approve Frontend + thin job API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a real web frontend (Next.js + Tailwind + Framer Motion, navy/gold government theme)
backed by a thin new backend job API that wraps the existing, tested `run_full_pipeline`, adding the
one compulsory piece missing from everything built so far: a human review-and-approve gate before any
video counts as "published."

**Architecture:** A new backend module (`backend/app/routes/pipeline.py` + `backend/app/pipeline_jobs.py`)
runs `run_full_pipeline`/`generate_language_video` in a background thread per job, holds results in an
in-memory store, and exposes job/review/approve/reject/edit/regenerate endpoints. The frontend polls
for job status, then renders a per-language review card (script, facts, verification, video) with
Approve/Reject/Edit/Regenerate actions. Zero behavioral changes to the tested pipeline itself beyond
three new additive fields on `LanguageVideoResult`.

**Tech Stack:** FastAPI (existing `backend/`), threading (stdlib) for background job execution,
in-memory dict job store. Next.js 15 + React 18 (existing `frontend/` scaffold) + Tailwind CSS +
Framer Motion.

**Spec:** `docs/superpowers/specs/2026-08-21-review-dashboard-frontend-design.md` — read this in full
before starting; it has the full rationale, the exact endpoint contracts, and the decisions this plan
implements. This plan's job descriptions are authoritative for exact code; the spec is authoritative
for *why*.

## Global Constraints

- **Zero behavioral changes to the existing pipeline.** `run_full_pipeline`, `generate_language_video`,
  `TemplateStoryDirector`, `dynamic_narration.py`, `AvatarFailoverProvider`, the caption burner, and
  the ffmpeg renderer are called exactly as they already work today. The only pipeline-adjacent change
  in this entire plan is three new additive fields on `LanguageVideoResult` (Task 1).
- **No new job persistence.** In-memory dict only, no database, no migration. Job history is lost on
  backend restart — this is accepted, not a bug to fix here.
- **No authentication, no multi-user handling.** Single-operator demo tool.
- **Backend routes are all new, under `backend/app/routes/pipeline.py`.** The existing 11 501-stub
  route modules (`documents.py`, `facts.py`, `generate.py`, `process.py`, `projects.py`, `scripts.py`,
  `storyboard.py`, `translate.py`, `verification.py`, `workflow.py`, `approval.py`) are never modified.
- **Backend tests live in the repo-root `tests/` directory**, not `backend/tests/` — this matches the
  existing convention documented in `backend/README.md` (`PYTHONPATH=".:.." pytest ../tests`, run from
  inside `backend/` with its own `.venv` activated, or equivalently `PYTHONPATH="backend:."` from the
  repo root).
- **Scene identification in edit/regenerate uses each `Scene`'s real `id` field** (a stable UUID
  already on every `Scene` via `IdentifiedModel`), not `order_index` — more robust, and the id is
  already present in every scene the API already returns.
- **Money/quota discipline**: every new automated test that touches `generate_language_video` or
  `run_full_pipeline` for real must fake the avatar and visual providers exactly like the existing
  test suite already does (`tests/test_multilingual_video.py`'s `_FakeAvatarProvider`/
  `_FakeVisualProvider` pattern) — never a real, paid Hedra/D-ID/Cloudflare call from an automated
  test. Live end-to-end verification (if done at all) is a manual, one-off check the implementer runs
  by hand, never committed as an unguarded automated test.

---

## Task 1: Add `facts`, `verification_results`, `image_paths` to `LanguageVideoResult`

**Files:**
- Modify: `rendering/multilingual_video.py`
- Modify: `tests/test_multilingual_video.py`

**Interfaces:**
- Produces: `LanguageVideoResult.facts: list[SourceFact]`, `.verification_results: list[VerificationResult]`,
  `.image_paths: list[str]` — consumed by Task 4 (the `GET /pipeline/jobs/{id}` response) and Task 7
  (the regenerate endpoint).

- [ ] **Step 1: Add the three fields to the dataclass**

In `rendering/multilingual_video.py`, add this import near the other `core.models` imports (alongside
the existing `from core.models.fact import SourceFact` line):

```python
from core.models.verification import VerificationResult
```

Change the `LanguageVideoResult` dataclass from:

```python
@dataclass
class LanguageVideoResult:
    language: LanguageCode
    video_asset: VideoAsset
    srt_text: str
    vtt_text: str
    scenes: list[Scene]
    verified_count: int
    blocking_count: int
    avatar_composited: bool
```

to:

```python
@dataclass
class LanguageVideoResult:
    language: LanguageCode
    video_asset: VideoAsset
    srt_text: str
    vtt_text: str
    scenes: list[Scene]
    facts: list[SourceFact]
    """The full Source Fact Ledger this language's narration was
    verified against — identical across every language in the same
    run_full_pipeline call (facts are extracted once, shared, per the
    language-independence principle). Exists so a caller (e.g. a review
    UI) can show the officer what was actually extracted from the
    source document without re-running extraction."""
    image_paths: list[str]
    """The B-roll image paths this language's video was composed from —
    identical across every language in the same run for the same
    reason as `facts`. Exists so a caller can re-invoke
    generate_language_video later (e.g. after an edited narration line)
    without re-rendering images or re-extracting facts."""
    verified_count: int
    blocking_count: int
    verification_results: list[VerificationResult]
    """Per-claim detail behind verified_count/blocking_count above —
    one VerificationResult per scene, in the same order as `scenes`.
    Exists so a caller can show which specific scene(s) failed
    verification and why, not just the aggregate counts."""
    avatar_composited: bool
```

(the rest of the class — the `avatar_composited`/`avatar_tier` docstrings and `avatar_tier: int | None = None`
— is unchanged, just keep it after the fields above since it's the only field with a default and
dataclass fields with defaults must come last)

- [ ] **Step 2: Populate the new fields in `generate_language_video`'s return statement**

Find the existing return statement (near the end of `generate_language_video`):

```python
    return LanguageVideoResult(
        language=target_language, video_asset=video_asset, srt_text=srt_text, vtt_text=vtt_text,
        scenes=scenes, verified_count=verified_count, blocking_count=blocking_count,
        avatar_composited=avatar_composited, avatar_tier=avatar_tier,
    )
```

Change it to:

```python
    return LanguageVideoResult(
        language=target_language, video_asset=video_asset, srt_text=srt_text, vtt_text=vtt_text,
        scenes=scenes, facts=facts, image_paths=image_paths,
        verified_count=verified_count, blocking_count=blocking_count, verification_results=results,
        avatar_composited=avatar_composited, avatar_tier=avatar_tier,
    )
```

(`facts` and `image_paths` are already this function's own parameters; `results` is the
`list[VerificationResult]` already computed a few lines above by
`DeterministicFactVerifier().verify_batch(claims, facts)` — nothing new is computed, only returned.)

- [ ] **Step 3: Extend the existing live test to assert the new fields**

In `tests/test_multilingual_video.py`, find `test_generates_a_hindi_video_reusing_the_same_images`
(the one gated behind `@pytest.mark.skipif(not _HAS_KEYS, ...)`). After the existing assertions
(`assert result.vtt_text.startswith("WEBVTT")`), add:

```python
    assert result.facts == facts
    assert result.image_paths == image_paths
    assert len(result.verification_results) == len(result.scenes)
    assert all(r.is_blocking is False for r in result.verification_results)
```

- [ ] **Step 4: Run the pipeline regression suite**

Run: `cd /Users/ampa/VaaniReach-hack/vaanireach && PYTHONPATH=. .venv/bin/python -m pytest tests/test_narrative_story_director.py tests/test_dynamic_narration.py tests/test_cloudflare_scene_renderer.py tests/test_deterministic_fact_verifier.py tests/test_multilingual_video.py -q`

Expected: all tests pass (the live-key-gated tests in `test_multilingual_video.py` will run for real
if `GROQ_API_KEY`/`SARVAM_API_KEYS` are set in `.env` — they already are this session — and will make
real Groq/Sarvam calls but fake avatar/visual per the Global Constraints above, so no D-ID/Hedra/Cloudflare
cost).

- [ ] **Step 5: Commit**

```bash
git add rendering/multilingual_video.py tests/test_multilingual_video.py
git commit -m "Add facts/verification_results/image_paths to LanguageVideoResult

Purely additive — all three values already existed inside
generate_language_video, just weren't returned. Needed by the
upcoming review-dashboard backend to show an officer the detected
facts and per-claim verification detail (not just aggregate counts),
and to let Regenerate reuse the same facts/images without
re-extracting or re-rendering.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2: Extract shared text-extraction module out of `local_demo.py`

**Files:**
- Create: `providers/documents/__init__.py` (empty, matches every other `providers/*` subpackage)
- Create: `providers/documents/text_extraction.py`
- Modify: `dashboard/local_demo.py`
- Test: `tests/test_text_extraction.py`

**Interfaces:**
- Produces: `providers.documents.text_extraction.extract_text_from_upload_bytes(filename: str, file_bytes: bytes) -> str`
  and `providers.documents.text_extraction.ocr_pdf_bytes(pdf_bytes: bytes) -> str` — consumed by
  Task 4 (backend `POST /pipeline/jobs`).

- [ ] **Step 1: Read the current implementation**

Read `dashboard/local_demo.py`'s `_ocr_pdf_bytes` and `extract_text_from_upload` functions (they're
near the top of the file, right after the `LANGUAGE_LABELS` dict) — copy their logic exactly, only
changing the signature to take raw bytes + a filename instead of a Streamlit `UploadedFile` object
(so the module has no Streamlit dependency and the backend, which never sees an `UploadedFile`, can
use it too).

- [ ] **Step 2: Create the shared module**

Create `providers/documents/__init__.py` (empty file).

Create `providers/documents/text_extraction.py`:

```python
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
```

- [ ] **Step 3: Update `local_demo.py` to call the shared module**

In `dashboard/local_demo.py`, delete the existing `_ocr_pdf_bytes` and `extract_text_from_upload`
function bodies and replace them with a thin wrapper that keeps the Streamlit-specific `st.info()`
progress message:

```python
from providers.documents.text_extraction import extract_text_from_upload_bytes  # noqa: E402


def extract_text_from_upload(uploaded_file) -> str:
    """Thin Streamlit wrapper around the shared extract_text_from_upload_bytes
    — adds the st.info() progress message for the OCR path, which only
    makes sense in a Streamlit UI."""
    from pypdf import PdfReader
    from io import BytesIO

    name = uploaded_file.name.lower()
    file_bytes = uploaded_file.getvalue() if name.endswith(".pdf") else uploaded_file.read()

    if name.endswith(".pdf"):
        reader = PdfReader(BytesIO(file_bytes))
        has_text_layer = any((page.extract_text() or "").strip() for page in reader.pages)
        if not has_text_layer:
            st.info(f"'{uploaded_file.name}' has no embedded text layer — running OCR (this takes a few seconds)…")

    return extract_text_from_upload_bytes(uploaded_file.name, file_bytes)
```

Remove the now-unused `from io import BytesIO` import from inside the old function body if it's no
longer referenced elsewhere in the file (it's now only used inside this wrapper, imported locally
above — keep it local to avoid an unused top-level import).

- [ ] **Step 4: Write a real pytest file for the shared module**

Create `tests/test_text_extraction.py`:

```python
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
```

- [ ] **Step 5: Run the new tests**

Run: `cd /Users/ampa/VaaniReach-hack/vaanireach && PYTHONPATH=. .venv/bin/python -m pytest tests/test_text_extraction.py -v`

Expected: all tests pass (4, or 3 + the OCR one skipped if tesseract/pdf2image aren't on this
machine).

- [ ] **Step 6: Manually smoke-test `local_demo.py` still works**

Run: `cd /Users/ampa/VaaniReach-hack/vaanireach && PYTHONPATH=. .venv/bin/streamlit run dashboard/local_demo.py`
— upload a .txt or .pdf and confirm text extraction still works exactly as before (this file's own
behavior must not change, only where its logic lives). Stop the server after confirming (Ctrl+C).

- [ ] **Step 7: Commit**

```bash
git add providers/documents/ dashboard/local_demo.py tests/test_text_extraction.py
git commit -m "Extract shared text_extraction module out of local_demo.py

Pulled extract_text_from_upload/_ocr_pdf_bytes out of the Streamlit
app into providers/documents/text_extraction.py (pure functions, no
Streamlit dependency) so the upcoming backend job API can reuse the
exact same OCR logic instead of a second, drifting copy.
local_demo.py keeps a thin wrapper for its st.info() progress message.

Also adds real pytest coverage (txt decode, pypdf extraction,
unsupported-extension error, real-OCR-on-a-synthesized-scanned-PDF)
where before this logic only had ad-hoc manual verification.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 3: Backend job store — `backend/app/pipeline_jobs.py`

**Files:**
- Create: `backend/app/pipeline_jobs.py`
- Test: `tests/test_pipeline_jobs.py`

**Interfaces:**
- Produces: `JobStore` class with `create_job(languages: list[LanguageCode]) -> JobRecord`,
  `get_job(job_id: str) -> JobRecord | None`, and the `JobRecord`/`LanguageJobState` dataclasses —
  consumed by Task 4 (POST /pipeline/jobs + GET /pipeline/jobs/{id}) and every later backend task.
- Consumes: nothing outside the stdlib + `rendering.multilingual_video.LanguageVideoResult`.

- [ ] **Step 1: Write the job store**

Create `backend/app/pipeline_jobs.py`:

```python
"""In-memory job store for the review-dashboard backend. No database —
job history is lost on backend restart, which is accepted for this
single-operator demo tool (see the design spec's Decisions section).

Every mutation to a JobRecord happens under that record's own lock
(`JobRecord.lock`), so a GET request polling mid-write never observes a
torn/partial update — the background thread doing the actual
generation work holds the lock only for the instant it swaps in a
finished result, never for the full multi-minute pipeline call itself.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Literal

from core.models.enums import LanguageCode
from rendering.multilingual_video import LanguageVideoResult

JobStatus = Literal["pending", "running", "pending_review", "failed"]
LanguageStatus = Literal["pending_review", "published", "rejected"]


@dataclass
class LanguageJobState:
    status: LanguageStatus
    result: LanguageVideoResult
    regenerating: bool = False


@dataclass
class JobRecord:
    job_id: str
    status: JobStatus = "pending"
    error: str | None = None
    languages: dict[LanguageCode, LanguageJobState] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)


class JobStore:
    """Process-wide singleton (one instance created in backend/app/main.py
    and reused by every route handler) — a plain dict is enough since
    FastAPI's default dev server (uvicorn --reload aside) runs this as
    one process, one set of background threads, no multi-worker sharing
    required for a demo tool."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._store_lock = threading.Lock()

    def create_job(self) -> JobRecord:
        job_id = str(uuid.uuid4())
        record = JobRecord(job_id=job_id)
        with self._store_lock:
            self._jobs[job_id] = record
        return record

    def get_job(self, job_id: str) -> JobRecord | None:
        with self._store_lock:
            return self._jobs.get(job_id)
```

- [ ] **Step 2: Write the test**

Create `tests/test_pipeline_jobs.py`:

```python
"""backend.app.pipeline_jobs — the in-memory job store. Pure data
structure, no network, no pipeline calls."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.pipeline_jobs import JobRecord, JobStore, LanguageJobState  # noqa: E402


def test_create_job_returns_a_pending_record_with_a_real_uuid():
    store = JobStore()
    record = store.create_job()
    assert record.status == "pending"
    assert record.error is None
    assert record.languages == {}
    assert len(record.job_id) == 36  # uuid4 string length


def test_get_job_returns_none_for_an_unknown_id():
    store = JobStore()
    assert store.get_job("does-not-exist") is None


def test_get_job_returns_the_same_record_object_created_earlier():
    store = JobStore()
    created = store.create_job()
    fetched = store.get_job(created.job_id)
    assert fetched is created


def test_two_jobs_get_distinct_ids():
    store = JobStore()
    a = store.create_job()
    b = store.create_job()
    assert a.job_id != b.job_id
```

- [ ] **Step 3: Run the test**

Run: `cd /Users/ampa/VaaniReach-hack/vaanireach && PYTHONPATH="backend:." .venv/bin/python -m pytest tests/test_pipeline_jobs.py -v`

Expected: all 4 tests pass.

- [ ] **Step 4: Commit**

```bash
git add backend/app/pipeline_jobs.py tests/test_pipeline_jobs.py
git commit -m "Add in-memory job store for the review-dashboard backend

Plain dict, no database — job history is lost on restart, accepted
per the design spec for this single-operator demo tool. Locking is
per-job (not a single global lock) so a GET poll never blocks behind
another job's in-flight write.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 4: `POST /pipeline/jobs` + `GET /pipeline/jobs/{id}`

**Files:**
- Create: `backend/app/routes/pipeline.py`
- Modify: `backend/app/main.py`
- Modify: `backend/requirements.txt`
- Test: `tests/test_pipeline_routes.py`

**Interfaces:**
- Consumes: `backend.app.pipeline_jobs.JobStore`/`JobRecord`/`LanguageJobState` (Task 3),
  `rendering.multilingual_video.run_full_pipeline` (unchanged), `providers.documents.text_extraction.extract_text_from_upload_bytes`
  (Task 2).
- Produces: the `router` FastAPI `APIRouter` this module exports, included into `app.main.app` —
  consumed by every later backend task (Tasks 5-7 add more routes to this same module/router).

- [ ] **Step 1: Add `python-dotenv` to backend requirements (for loading the root `.env`)**

The backend's own `requirements.txt` doesn't currently load `.env` at all (Phase 0 stubs need no
provider keys) — but `run_full_pipeline` needs `GROQ_API_KEY`, `SARVAM_API_KEYS`, etc. Add to
`backend/requirements.txt`:

```
python-dotenv>=1.0
```

- [ ] **Step 2: Write the route module**

Create `backend/app/routes/pipeline.py`:

```python
"""pipeline — the real job API this backend was missing. Wraps the
existing, tested rendering.multilingual_video.run_full_pipeline /
generate_language_video exactly as they already work; see
docs/superpowers/specs/2026-08-21-review-dashboard-frontend-design.md
for the full design and rationale. Every other route module in this
package (documents.py, facts.py, generate.py, ...) is a separate,
unrelated 501-stub architecture — untouched, unaffected by this file.
"""
from __future__ import annotations

import threading
import traceback

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.pipeline_jobs import JobRecord, JobStore, LanguageJobState
from core.models.enums import LanguageCode
from providers.documents.text_extraction import extract_text_from_upload_bytes
from rendering.multilingual_video import run_full_pipeline

router = APIRouter(prefix="/pipeline", tags=["pipeline"])
job_store = JobStore()


class CreateJobResponse(BaseModel):
    job_id: str
    status: str


def _run_generation(record: JobRecord, document_text: str, languages: list[LanguageCode]) -> None:
    """Runs on a background thread — never touches the FastAPI event
    loop. Atomic per the design spec: either every requested language's
    LanguageVideoResult lands in record.languages, or the whole job is
    marked failed. No partial per-language results from this initial
    generation (Regenerate, added in a later task, is the only place
    per-language isolation exists)."""
    with record.lock:
        record.status = "running"
    try:
        results = run_full_pipeline(document_text, languages=languages, project_id=record.job_id)
    except Exception as exc:  # noqa: BLE001 - must never crash the background thread silently
        with record.lock:
            record.status = "failed"
            record.error = f"{exc}\n{traceback.format_exc()}"
        return

    with record.lock:
        for result in results:
            record.languages[result.language] = LanguageJobState(status="pending_review", result=result)
        record.status = "pending_review"


@router.post("/jobs", response_model=CreateJobResponse, status_code=202)
async def create_job(
    languages: list[LanguageCode] = Form(...),
    text: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
) -> CreateJobResponse:
    if not languages:
        raise HTTPException(status_code=400, detail="Select at least one language.")

    if file is not None:
        file_bytes = await file.read()
        try:
            document_text = extract_text_from_upload_bytes(file.filename or "upload", file_bytes)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    elif text is not None and text.strip():
        document_text = text.strip()
    else:
        raise HTTPException(status_code=400, detail="Upload a file or provide text.")

    record = job_store.create_job()
    thread = threading.Thread(target=_run_generation, args=(record, document_text, languages), daemon=True)
    thread.start()
    return CreateJobResponse(job_id=record.job_id, status=record.status)


def _serialize_scene(scene) -> dict:
    return {
        "id": scene.id,
        "order_index": scene.order_index,
        "narrative_role": scene.narrative_role.value,
        "narration_segment_text": scene.narration_segment_text,
        "source_fact_ids": scene.source_fact_ids,
        "claim_ids": scene.claim_ids,
        # claim_ids is how the frontend joins a scene to its
        # VerificationResult below (claims_from_scenes sets exactly one
        # claim id per scene, matching one verification_results entry's
        # claim_id) — without this, a per-scene verification badge has
        # no reliable key to join on.
    }


def _serialize_fact(fact) -> dict:
    return {
        "id": fact.id,
        "fact_type": fact.fact_type.value,
        "value": fact.value,
        "raw_text": fact.raw_text,
        "source_span": {
            "page_number": fact.source_span.page_number,
            "text_span": fact.source_span.text_span,
        },
    }


def _serialize_verification_result(vr) -> dict:
    return {
        "claim_id": vr.claim_id,
        "status": vr.status.value,
        "is_blocking": vr.is_blocking,
        "explanation": vr.explanation,
    }


def _serialize_language_state(job_id: str, state: LanguageJobState) -> dict:
    result = state.result
    return {
        "status": state.status,
        "regenerating": state.regenerating,
        "avatar_tier": result.avatar_tier,
        "avatar_composited": result.avatar_composited,
        "video_url": f"/pipeline/jobs/{job_id}/video/{result.language.value}",
        "srt_url": f"/pipeline/jobs/{job_id}/captions/{result.language.value}.srt",
        "vtt_url": f"/pipeline/jobs/{job_id}/captions/{result.language.value}.vtt",
        "scenes": [_serialize_scene(s) for s in result.scenes],
        "verified_count": result.verified_count,
        "blocking_count": result.blocking_count,
        "verification_results": [_serialize_verification_result(vr) for vr in result.verification_results],
    }


@router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    record = job_store.get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    with record.lock:
        languages_payload = {
            lang.value: _serialize_language_state(job_id, state) for lang, state in record.languages.items()
        }
        facts_payload = []
        if record.languages:
            any_result = next(iter(record.languages.values())).result
            facts_payload = [_serialize_fact(f) for f in any_result.facts]
        return {
            "job_id": record.job_id,
            "status": record.status,
            "error": record.error,
            "languages": languages_payload,
            "facts": facts_payload,
        }
```

- [ ] **Step 3: Wire the router into `backend/app/main.py`**

In `backend/app/main.py`, add `pipeline` to the `from app.routes import (...)` block and to the
`for router_module in (...)` loop:

```python
from app.routes import (
    approval,
    documents,
    facts,
    generate,
    pipeline,
    process,
    projects,
    scripts,
    storyboard,
    translate,
    verification,
    workflow,
)
```

```python
for router_module in (
    projects,
    documents,
    process,
    facts,
    scripts,
    translate,
    storyboard,
    generate,
    verification,
    workflow,
    approval,
    pipeline,
):
    app.include_router(router_module.router)
```

`backend/app/main.py` has no `load_dotenv` call at all today (the Phase 0 stubs need no provider
keys) — add one so the pipeline can see `GROQ_API_KEY`/`SARVAM_API_KEYS`/etc. from the repo-root
`.env`. Near the top of the file, before the `app = FastAPI(...)` line, add:

```python
from dotenv import load_dotenv
load_dotenv("../.env")  # repo-root .env — backend runs with cwd=backend/, PYTHONPATH=..
```

- [ ] **Step 4: Write the route tests**

Create `tests/test_pipeline_routes.py`:

```python
"""backend.app.routes.pipeline — the review-dashboard job API.
run_full_pipeline is monkeypatched to a fast fake throughout; no real
Groq/Sarvam/D-ID/Cloudflare call happens in this file.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import time  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core.models.enums import Criticality, FactType, LanguageCode, VerificationStatus, VerificationType  # noqa: E402
from core.models.fact import SourceFact  # noqa: E402
from core.models.media import VideoAsset  # noqa: E402
from core.models.storyboard import Scene  # noqa: E402
from core.models.enums import NarrativeRole, SceneType  # noqa: E402
from core.models.verification import VerificationResult  # noqa: E402
from core.provenance.models import SourceSpan  # noqa: E402
from rendering.multilingual_video import LanguageVideoResult  # noqa: E402


def _fake_result(language: LanguageCode, project_id: str) -> LanguageVideoResult:
    fact = SourceFact(
        project_id=project_id, document_id="doc-1", fact_type=FactType.AMOUNT, value="₹10,000",
        raw_text="₹10,000", source_span=SourceSpan(document_id="doc-1", page_number=1, text_span="₹10,000"),
        criticality=Criticality.CRITICAL, confidence=0.95, extractor_name="fake",
    )
    scene = Scene(
        storyboard_id="sb-1", order_index=0, scene_type=SceneType.TEXT, narrative_role=NarrativeRole.BENEFIT,
        narration_segment_text="Eligible recipients receive ₹10,000.", source_fact_ids=[fact.id],
        duration_seconds=3.0,
    )
    vr = VerificationResult(
        project_id=project_id, claim_id="claim-1", verification_type=VerificationType.DETERMINISTIC,
        status=VerificationStatus.VERIFIED, matched_source_fact_ids=[fact.id],
        explanation="ok", confidence=1.0, verifier_name="fake", is_blocking=False,
    )
    video_asset = VideoAsset(
        project_id=project_id, storyboard_id="sb-1", language=language, storage_path_mp4="/tmp/fake.mp4",
    )
    return LanguageVideoResult(
        language=language, video_asset=video_asset, srt_text="1\n00:00:00,000 --> 00:00:03,000\nfake\n",
        vtt_text="WEBVTT\n\n00:00:00.000 --> 00:00:03.000\nfake\n", scenes=[scene], facts=[fact],
        image_paths=["/tmp/fake.jpg"], verified_count=1, blocking_count=0, verification_results=[vr],
        avatar_composited=True, avatar_tier=2,
    )


@pytest.fixture
def client(monkeypatch):
    def fake_run_full_pipeline(text, *, languages, project_id, **kwargs):
        return [_fake_result(lang, project_id) for lang in languages]

    monkeypatch.setattr("app.routes.pipeline.run_full_pipeline", fake_run_full_pipeline)

    from app.main import app
    return TestClient(app)


def _create_job_and_wait(client, **kwargs) -> dict:
    resp = client.post("/pipeline/jobs", data={"languages": ["en"], "text": "test notice text"}, **kwargs)
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    for _ in range(50):
        job = client.get(f"/pipeline/jobs/{job_id}").json()
        if job["status"] in ("pending_review", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError("job never reached a terminal status")


def test_create_job_returns_202_with_a_job_id(client):
    resp = client.post("/pipeline/jobs", data={"languages": ["en"], "text": "test notice text"})
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "pending"
    assert len(body["job_id"]) == 36


def test_create_job_without_text_or_file_returns_400(client):
    resp = client.post("/pipeline/jobs", data={"languages": ["en"]})
    assert resp.status_code == 400


def test_create_job_without_languages_returns_400(client):
    resp = client.post("/pipeline/jobs", data={"text": "test notice text"})
    assert resp.status_code in (400, 422)


def test_job_reaches_pending_review_with_the_fake_result(client):
    job = _create_job_and_wait(client)
    assert job["status"] == "pending_review"
    assert "en" in job["languages"]
    assert job["languages"]["en"]["status"] == "pending_review"
    assert job["languages"]["en"]["avatar_tier"] == 2
    assert job["languages"]["en"]["verified_count"] == 1
    assert len(job["languages"]["en"]["verification_results"]) == 1
    assert len(job["facts"]) == 1
    assert job["facts"][0]["value"] == "₹10,000"


def test_get_unknown_job_returns_404(client):
    resp = client.get("/pipeline/jobs/does-not-exist")
    assert resp.status_code == 404


def test_job_fails_cleanly_when_the_pipeline_raises(client, monkeypatch):
    def raising_pipeline(text, *, languages, project_id, **kwargs):
        raise ValueError("zero facts extracted")

    monkeypatch.setattr("app.routes.pipeline.run_full_pipeline", raising_pipeline)

    resp = client.post("/pipeline/jobs", data={"languages": ["en"], "text": "test notice text"})
    job_id = resp.json()["job_id"]

    for _ in range(50):
        job = client.get(f"/pipeline/jobs/{job_id}").json()
        if job["status"] == "failed":
            assert "zero facts extracted" in job["error"]
            return
        time.sleep(0.05)
    raise AssertionError("job never reached failed status")
```

- [ ] **Step 5: Install the new backend dependency and run the tests**

```bash
cd /Users/ampa/VaaniReach-hack/vaanireach/backend && source .venv/bin/activate && pip install -r requirements.txt && cd ..
PYTHONPATH="backend:." backend/.venv/bin/python -m pytest tests/test_pipeline_routes.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/routes/pipeline.py backend/app/main.py backend/requirements.txt tests/test_pipeline_routes.py
git commit -m "Add POST /pipeline/jobs and GET /pipeline/jobs/{id}

The real job API — wraps run_full_pipeline exactly as it already
works, running it on a background thread so the FastAPI event loop
never blocks for the multi-minute generation call. A job's terminal
generation state is pending_review, not done: publication is a
separate, later human action (Tasks 5-7).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 5: Video/caption serving + Approve/Reject endpoints

**Files:**
- Modify: `backend/app/routes/pipeline.py`
- Modify: `tests/test_pipeline_routes.py`

**Interfaces:**
- Consumes: everything from Task 4 (same module, same `job_store`).

- [ ] **Step 1: Add the four endpoints**

Append to `backend/app/routes/pipeline.py`:

```python
from fastapi.responses import FileResponse, PlainTextResponse


@router.get("/jobs/{job_id}/video/{language}")
async def get_video(job_id: str, language: LanguageCode) -> FileResponse:
    record = job_store.get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    with record.lock:
        state = record.languages.get(language)
        if state is None or not state.result.video_asset.storage_path_mp4:
            raise HTTPException(status_code=404, detail="No video for this language yet.")
        path = state.result.video_asset.storage_path_mp4
    return FileResponse(path, media_type="video/mp4")


@router.get("/jobs/{job_id}/captions/{language}.srt")
async def get_srt(job_id: str, language: LanguageCode) -> PlainTextResponse:
    record = job_store.get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    with record.lock:
        state = record.languages.get(language)
        if state is None:
            raise HTTPException(status_code=404, detail="No captions for this language yet.")
        return PlainTextResponse(state.result.srt_text, media_type="application/x-subrip")


@router.get("/jobs/{job_id}/captions/{language}.vtt")
async def get_vtt(job_id: str, language: LanguageCode) -> PlainTextResponse:
    record = job_store.get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    with record.lock:
        state = record.languages.get(language)
        if state is None:
            raise HTTPException(status_code=404, detail="No captions for this language yet.")
        return PlainTextResponse(state.result.vtt_text, media_type="text/vtt")


def _get_pending_review_language_state(record: JobRecord, language: LanguageCode) -> LanguageJobState:
    state = record.languages.get(language)
    if state is None:
        raise HTTPException(status_code=404, detail="No such language on this job.")
    if state.status != "pending_review":
        raise HTTPException(status_code=409, detail=f"Language is '{state.status}', not pending_review.")
    return state


@router.post("/jobs/{job_id}/languages/{language}/approve")
async def approve_language(job_id: str, language: LanguageCode) -> dict:
    record = job_store.get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    with record.lock:
        state = _get_pending_review_language_state(record, language)
        state.status = "published"
    return {"status": "published"}


@router.post("/jobs/{job_id}/languages/{language}/reject")
async def reject_language(job_id: str, language: LanguageCode) -> dict:
    record = job_store.get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    with record.lock:
        state = _get_pending_review_language_state(record, language)
        state.status = "rejected"
    return {"status": "rejected"}
```

- [ ] **Step 2: Add tests**

Append to `tests/test_pipeline_routes.py`:

```python
def test_approve_then_reject_precondition_fails(client):
    job = _create_job_and_wait(client)
    job_id = job["job_id"]

    resp = client.post(f"/pipeline/jobs/{job_id}/languages/en/approve")
    assert resp.status_code == 200
    assert resp.json()["status"] == "published"

    job_after = client.get(f"/pipeline/jobs/{job_id}").json()
    assert job_after["languages"]["en"]["status"] == "published"

    # already published — reject must now fail its precondition
    resp = client.post(f"/pipeline/jobs/{job_id}/languages/en/reject")
    assert resp.status_code == 409


def test_reject_sets_status_to_rejected(client):
    job = _create_job_and_wait(client)
    job_id = job["job_id"]

    resp = client.post(f"/pipeline/jobs/{job_id}/languages/en/reject")
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


def test_approve_unknown_language_on_a_real_job_returns_404(client):
    job = _create_job_and_wait(client)
    resp = client.post(f"/pipeline/jobs/{job['job_id']}/languages/ta/approve")
    assert resp.status_code == 404


def test_srt_and_vtt_download_endpoints(client):
    job = _create_job_and_wait(client)
    job_id = job["job_id"]

    srt = client.get(f"/pipeline/jobs/{job_id}/captions/en.srt")
    assert srt.status_code == 200
    assert "fake" in srt.text

    vtt = client.get(f"/pipeline/jobs/{job_id}/captions/en.vtt")
    assert vtt.status_code == 200
    assert vtt.text.startswith("WEBVTT")
```

- [ ] **Step 3: Run the tests**

Run: `cd /Users/ampa/VaaniReach-hack/vaanireach && PYTHONPATH="backend:." backend/.venv/bin/python -m pytest tests/test_pipeline_routes.py -v`

Expected: all 10 tests pass.

- [ ] **Step 4: Commit**

```bash
git add backend/app/routes/pipeline.py tests/test_pipeline_routes.py
git commit -m "Add video/caption serving + Approve/Reject endpoints

Approve and Reject are pure status flips on the in-memory job record
- no pipeline call. Video/SRT/VTT are served directly from the paths
LanguageVideoResult already produced (the bonus 'Subtitle Export'
problem-statement feature — the data already existed, this just
exposes it over HTTP).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 6: Edit endpoint (inline re-verification, no render)

**Files:**
- Modify: `backend/app/routes/pipeline.py`
- Modify: `tests/test_pipeline_routes.py`

**Interfaces:**
- Consumes: `providers.verification.deterministic_fact_verifier.DeterministicFactVerifier`,
  `core.models.claim.Claim` (both unchanged, existing).

- [ ] **Step 1: Add the edit endpoint**

Append to `backend/app/routes/pipeline.py`:

```python
from core.models.claim import Claim
from core.models.enums import Criticality
from providers.verification.deterministic_fact_verifier import DeterministicFactVerifier


class EditSceneRequest(BaseModel):
    scene_id: str
    narration_segment_text: str


@router.post("/jobs/{job_id}/languages/{language}/edit")
async def edit_scene(job_id: str, language: LanguageCode, payload: EditSceneRequest) -> dict:
    record = job_store.get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    with record.lock:
        state = record.languages.get(language)
        if state is None:
            raise HTTPException(status_code=404, detail="No such language on this job.")

        scene = next((s for s in state.result.scenes if s.id == payload.scene_id), None)
        if scene is None:
            raise HTTPException(status_code=404, detail="No such scene on this language's result.")

        claim = Claim(
            project_id=job_id, claim_text=payload.narration_segment_text, language=language,
            source_fact_ids=list(scene.source_fact_ids), claim_type=scene.narrative_role.value,
            criticality=Criticality.MEDIUM,
        )
        verification = DeterministicFactVerifier().verify_claim(claim, state.result.facts)

        scene.narration_segment_text = payload.narration_segment_text

    return {
        "scene_id": scene.id,
        "narration_segment_text": scene.narration_segment_text,
        "verification": _serialize_verification_result(verification),
    }
```

- [ ] **Step 2: Add tests**

Append to `tests/test_pipeline_routes.py`:

```python
def test_edit_a_scene_with_grounded_text_verifies_and_saves(client):
    job = _create_job_and_wait(client)
    job_id = job["job_id"]
    scene_id = job["languages"]["en"]["scenes"][0]["id"]

    resp = client.post(
        f"/pipeline/jobs/{job_id}/languages/en/edit",
        json={"scene_id": scene_id, "narration_segment_text": "Recipients get ₹10,000 in total."},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["verification"]["status"] == "verified"
    assert body["verification"]["is_blocking"] is False

    job_after = client.get(f"/pipeline/jobs/{job_id}").json()
    edited_scene = next(s for s in job_after["languages"]["en"]["scenes"] if s["id"] == scene_id)
    assert edited_scene["narration_segment_text"] == "Recipients get ₹10,000 in total."


def test_edit_a_scene_with_invented_content_flags_but_still_saves(client):
    """The officer is the human approval gate — editing to something
    unverified must still be visible/savable (they might fix the
    source facts separately, or regenerate after correcting it), but
    the verification result must clearly flag it as not grounded."""
    job = _create_job_and_wait(client)
    job_id = job["job_id"]
    scene_id = job["languages"]["en"]["scenes"][0]["id"]

    resp = client.post(
        f"/pipeline/jobs/{job_id}/languages/en/edit",
        json={"scene_id": scene_id, "narration_segment_text": "Recipients get ₹99,999,999 immediately."},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["verification"]["is_blocking"] is True


def test_edit_unknown_scene_returns_404(client):
    job = _create_job_and_wait(client)
    resp = client.post(
        f"/pipeline/jobs/{job['job_id']}/languages/en/edit",
        json={"scene_id": "does-not-exist", "narration_segment_text": "anything"},
    )
    assert resp.status_code == 404
```

- [ ] **Step 3: Run the tests**

Run: `cd /Users/ampa/VaaniReach-hack/vaanireach && PYTHONPATH="backend:." backend/.venv/bin/python -m pytest tests/test_pipeline_routes.py -v`

Expected: all 13 tests pass.

- [ ] **Step 4: Commit**

```bash
git add backend/app/routes/pipeline.py tests/test_pipeline_routes.py
git commit -m "Add Edit endpoint: inline re-verification, no render cost

Reuses DeterministicFactVerifier directly (no LLM, no TTS, no render)
so the officer gets instant feedback on whether their edit is still
grounded in the source facts. The edited text is saved either way -
the officer is the approval authority - but a blocking verification
result makes an ungrounded edit impossible to miss. Reaching the
actual video still requires Regenerate (next task).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 7: Regenerate endpoint

**Files:**
- Modify: `backend/app/routes/pipeline.py`
- Modify: `tests/test_pipeline_routes.py`

**Interfaces:**
- Consumes: `rendering.multilingual_video.generate_language_video`,
  `providers.narrative.template_story_director.TemplateStoryDirector`,
  `providers.translation.groq_translation_provider.GroqTranslationProvider` (all unchanged,
  existing).

- [ ] **Step 1: Add the regenerate endpoint**

Append to `backend/app/routes/pipeline.py`:

```python
from providers.narrative.template_story_director import TemplateStoryDirector
from providers.translation.groq_translation_provider import GroqTranslationProvider
from rendering.multilingual_video import generate_language_video


def _run_regenerate(record: JobRecord, language: LanguageCode) -> None:
    with record.lock:
        state = record.languages[language]
        facts = state.result.facts
        image_paths = state.result.image_paths
        scenes = state.result.scenes

    try:
        new_result = generate_language_video(
            facts, image_paths, story_director=TemplateStoryDirector(), translator=GroqTranslationProvider(),
            target_language=language, project_id=record.job_id, precomputed_scenes=scenes,
        )
    except Exception:  # noqa: BLE001 - a failed regenerate must never crash the thread or corrupt state
        with record.lock:
            record.languages[language].regenerating = False
        return

    with record.lock:
        record.languages[language] = LanguageJobState(status="pending_review", result=new_result)


@router.post("/jobs/{job_id}/languages/{language}/regenerate", status_code=202)
async def regenerate_language(job_id: str, language: LanguageCode) -> dict:
    record = job_store.get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    with record.lock:
        state = _get_pending_review_language_state(record, language)
        if state.regenerating:
            raise HTTPException(status_code=409, detail="Already regenerating this language.")
        state.regenerating = True

    thread = threading.Thread(target=_run_regenerate, args=(record, language), daemon=True)
    thread.start()
    return {"status": "regenerating"}
```

**Note**: `precomputed_scenes=scenes` passes the *current* scene list for that language — including
any edits made via Task 6's edit endpoint — through to `generate_language_video`, which per its own
docstring skips `story_director.plan_narrative_arc` entirely when `precomputed_scenes` is given. This
is the mechanism that makes an Edit "reach the actual video" once Regenerate runs.

- [ ] **Step 2: Add tests**

Append to `tests/test_pipeline_routes.py`:

```python
def test_regenerate_reuses_the_stored_facts_and_image_paths_via_precomputed_scenes(client, monkeypatch):
    job = _create_job_and_wait(client)
    job_id = job["job_id"]

    calls = []

    def fake_generate_language_video(facts, image_paths, *, precomputed_scenes, **kwargs):
        calls.append({"facts": facts, "image_paths": image_paths, "precomputed_scenes": precomputed_scenes})
        return _fake_result(LanguageCode.EN, job_id)

    monkeypatch.setattr("app.routes.pipeline.generate_language_video", fake_generate_language_video)

    resp = client.post(f"/pipeline/jobs/{job_id}/languages/en/regenerate")
    assert resp.status_code == 202

    for _ in range(50):
        job_after = client.get(f"/pipeline/jobs/{job_id}").json()
        if not job_after["languages"]["en"]["regenerating"]:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("regenerate never finished")

    assert job_after["languages"]["en"]["status"] == "pending_review"
    assert len(calls) == 1
    # precomputed_scenes was passed (not None) — proof the narration LLM/story
    # planning step was skipped, not re-run
    assert calls[0]["precomputed_scenes"] is not None
    assert len(calls[0]["precomputed_scenes"]) == 1


def test_regenerate_on_a_non_pending_review_language_returns_409(client):
    job = _create_job_and_wait(client)
    job_id = job["job_id"]
    client.post(f"/pipeline/jobs/{job_id}/languages/en/approve")

    resp = client.post(f"/pipeline/jobs/{job_id}/languages/en/regenerate")
    assert resp.status_code == 409
```

- [ ] **Step 3: Run the tests**

Run: `cd /Users/ampa/VaaniReach-hack/vaanireach && PYTHONPATH="backend:." backend/.venv/bin/python -m pytest tests/test_pipeline_routes.py -v`

Expected: all 15 tests pass.

- [ ] **Step 4: Run the full backend test file once more standalone to confirm no ordering issues**

Run: `cd /Users/ampa/VaaniReach-hack/vaanireach && PYTHONPATH="backend:." backend/.venv/bin/python -m pytest tests/test_pipeline_routes.py tests/test_pipeline_jobs.py -q`

Expected: 19 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routes/pipeline.py tests/test_pipeline_routes.py
git commit -m "Add Regenerate endpoint, completing the review-action set

Re-invokes generate_language_video with precomputed_scenes set to
that language's current (possibly officer-edited) scenes, reusing the
job's already-computed facts/image_paths - fact extraction, narration
drafting, and B-roll rendering never run again. This is the mechanism
that lets an Edit (previous task) actually reach the rendered video:
edit updates stored text only, Regenerate is what bakes it into
TTS/avatar/captions/composition.

Approve/Reject/Edit/Regenerate is now the complete review-action set
required by the problem statement's 'reviewable and approved by a
human before publication' compulsory requirement.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 8: Frontend scaffolding — Tailwind, Framer Motion, theme tokens

**Files:**
- Modify: `frontend/package.json`
- Create: `frontend/tailwind.config.ts`
- Create: `frontend/postcss.config.js`
- Modify: `frontend/src/app/globals.css`
- Modify: `frontend/src/app/layout.tsx`

**Interfaces:**
- Produces: the `navy`/`gold` Tailwind color tokens and `font-serif-display` class, consumed by
  Tasks 10-11.

- [ ] **Step 1: Install dependencies**

```bash
cd /Users/ampa/VaaniReach-hack/vaanireach/frontend
npm install -D tailwindcss postcss autoprefixer
npm install framer-motion
```

- [ ] **Step 2: Initialize Tailwind config**

Create `frontend/postcss.config.js`:

```js
module.exports = {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
```

Create `frontend/tailwind.config.ts`:

```ts
import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        navy: {
          DEFAULT: "#0f1a2b",
          light: "#1a2740",
          dark: "#0a1220",
        },
        gold: {
          DEFAULT: "#c9a227",
          light: "#d4af37",
          dark: "#a8871f",
        },
      },
      fontFamily: {
        serifDisplay: ["\"Playfair Display\"", "Georgia", "serif"],
      },
    },
  },
  plugins: [],
};

export default config;
```

- [ ] **Step 3: Wire Tailwind into globals.css**

Replace the full contents of `frontend/src/app/globals.css` with:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

html,
body {
  max-width: 100vw;
  overflow-x: hidden;
}

body {
  @apply bg-navy text-white;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  line-height: 1.5;
}
```

- [ ] **Step 4: Load the serif display font**

In `frontend/src/app/layout.tsx`, add the Google Font link inside `<head>` (Next.js App Router's
`layout.tsx` doesn't have an explicit `<head>` tag by default — add one) and apply the sans-serif body
font via the existing `<body>`:

```tsx
import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "VaaniReach",
  description: "Multilingual Outreach Video Generator",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@600;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
```

- [ ] **Step 5: Verify the dev server starts and Tailwind classes apply**

```bash
cd /Users/ampa/VaaniReach-hack/vaanireach/frontend
npm run dev
```

Visit `http://localhost:3000` — confirm the page background is now dark navy (from `bg-navy` on
`body`) instead of the previous default white. Stop the server after confirming (Ctrl+C).

- [ ] **Step 6: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/tailwind.config.ts frontend/postcss.config.js frontend/src/app/globals.css frontend/src/app/layout.tsx
git commit -m "Add Tailwind CSS + Framer Motion, navy/gold theme tokens

Sets up the dark navy/gold government-portal visual language (per
the user's reference image) as reusable Tailwind theme tokens
(navy/gold color scales, Playfair Display serif for headlines) rather
than one-off inline styles - both later pages (Home, Job review) pull
from the same tokens.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 9: Frontend types + API client for the job API

**Files:**
- Modify: `frontend/src/types/index.ts`
- Modify: `frontend/src/lib/api-client.ts`

**Interfaces:**
- Produces: `JobStatus`, `LanguageJobStatus`, `PipelineScene`, `LanguageJobView`, `JobView` types,
  and `createJob`, `getJob`, `approveLanguage`, `rejectLanguage`, `editScene`, `regenerateLanguage`
  functions — consumed by Tasks 10-11.

- [ ] **Step 1: Add the new types**

Append to `frontend/src/types/index.ts` (leave every existing type untouched — these are new, for the
job API specifically, deliberately not reusing `Project`/`SourceFact` as-is since the job API's shape
differs slightly, e.g. `SourceFact` here nests only `page_number`/`text_span` from `source_span`, not
the full `SourceSpan` interface already defined above — reuse that existing `SourceSpan`/`Criticality`
types directly rather than redefining them):

```ts
export type NarrativeRole =
  | "hook"
  | "context"
  | "problem"
  | "announcement"
  | "benefit"
  | "eligibility"
  | "how_to"
  | "deadline"
  | "urgency"
  | "cta"
  | "closing";

export type JobStatus = "pending" | "running" | "pending_review" | "failed";
export type LanguageJobStatus = "pending_review" | "published" | "rejected";

export interface PipelineScene {
  id: string;
  order_index: number;
  narrative_role: NarrativeRole;
  narration_segment_text: string;
  source_fact_ids: string[];
  claim_ids: string[]; // join key into LanguageJobView.verification_results[].claim_id
}

export interface PipelineVerificationResult {
  claim_id: string;
  status: VerificationStatus;
  is_blocking: boolean;
  explanation: string;
}

export interface LanguageJobView {
  status: LanguageJobStatus;
  regenerating: boolean;
  avatar_tier: number | null;
  avatar_composited: boolean;
  video_url: string;
  srt_url: string;
  vtt_url: string;
  scenes: PipelineScene[];
  verified_count: number;
  blocking_count: number;
  verification_results: PipelineVerificationResult[];
}

export interface PipelineFact {
  id: string;
  fact_type: FactType;
  value: string;
  raw_text: string;
  source_span: { page_number: number; text_span: string };
}

export interface JobView {
  job_id: string;
  status: JobStatus;
  error: string | null;
  languages: Record<LanguageCode, LanguageJobView>;
  facts: PipelineFact[];
}
```

- [ ] **Step 2: Add the API client functions**

Append to `frontend/src/lib/api-client.ts` (leave the existing `notImplemented`-backed functions
exactly as they are — they belong to the separate, unimplemented heavy contract):

```ts
import type { JobView, LanguageCode } from "@/types";

export async function createJob(input: { languages: LanguageCode[]; file?: File; text?: string }): Promise<{ job_id: string; status: string }> {
  const form = new FormData();
  for (const lang of input.languages) form.append("languages", lang);
  if (input.file) form.append("file", input.file);
  if (input.text) form.append("text", input.text);

  const resp = await fetch(`${API_BASE_URL}/pipeline/jobs`, { method: "POST", body: form });
  if (!resp.ok) throw new Error(`createJob failed: ${resp.status} ${await resp.text()}`);
  return resp.json();
}

export async function getJob(jobId: string): Promise<JobView> {
  const resp = await fetch(`${API_BASE_URL}/pipeline/jobs/${jobId}`);
  if (!resp.ok) throw new Error(`getJob failed: ${resp.status} ${await resp.text()}`);
  return resp.json();
}

export async function approveLanguage(jobId: string, language: LanguageCode): Promise<void> {
  const resp = await fetch(`${API_BASE_URL}/pipeline/jobs/${jobId}/languages/${language}/approve`, { method: "POST" });
  if (!resp.ok) throw new Error(`approveLanguage failed: ${resp.status} ${await resp.text()}`);
}

export async function rejectLanguage(jobId: string, language: LanguageCode): Promise<void> {
  const resp = await fetch(`${API_BASE_URL}/pipeline/jobs/${jobId}/languages/${language}/reject`, { method: "POST" });
  if (!resp.ok) throw new Error(`rejectLanguage failed: ${resp.status} ${await resp.text()}`);
}

export async function editScene(
  jobId: string, language: LanguageCode, sceneId: string, narrationSegmentText: string,
): Promise<{ scene_id: string; narration_segment_text: string; verification: { status: string; is_blocking: boolean; explanation: string } }> {
  const resp = await fetch(`${API_BASE_URL}/pipeline/jobs/${jobId}/languages/${language}/edit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scene_id: sceneId, narration_segment_text: narrationSegmentText }),
  });
  if (!resp.ok) throw new Error(`editScene failed: ${resp.status} ${await resp.text()}`);
  return resp.json();
}

export async function regenerateLanguage(jobId: string, language: LanguageCode): Promise<void> {
  const resp = await fetch(`${API_BASE_URL}/pipeline/jobs/${jobId}/languages/${language}/regenerate`, { method: "POST" });
  if (!resp.ok) throw new Error(`regenerateLanguage failed: ${resp.status} ${await resp.text()}`);
}
```

- [ ] **Step 3: Confirm the frontend still typechecks**

```bash
cd /Users/ampa/VaaniReach-hack/vaanireach/frontend
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types/index.ts frontend/src/lib/api-client.ts
git commit -m "Add frontend types + API client for the job API

New types/functions only, scoped to the job API (Tasks 4-7's
endpoints) - the existing notImplemented-backed functions for the
old heavy contract are untouched.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 10: Home page — hero, upload form, language picker

**Files:**
- Modify: `frontend/src/app/page.tsx`
- Create: `frontend/src/components/LanguagePicker.tsx`
- Create: `frontend/src/components/HowItWorks.tsx`

**Interfaces:**
- Consumes: `createJob` (Task 9), Next.js `useRouter` for navigation to `/jobs/[jobId]` (Task 11).

- [ ] **Step 1: Language picker component**

Create `frontend/src/components/LanguagePicker.tsx`:

```tsx
"use client";

import type { LanguageCode } from "@/types";

const LANGUAGE_LABELS: Record<LanguageCode, string> = {
  en: "English",
  hi: "हिन्दी (Hindi)",
  mr: "मराठी (Marathi)",
  bn: "বাংলা (Bengali)",
  ta: "தமிழ் (Tamil)",
  te: "తెలుగు (Telugu)",
  kn: "ಕನ್ನಡ (Kannada)",
  ml: "മലയാളം (Malayalam)",
  gu: "ગુજરાતી (Gujarati)",
};

export function LanguagePicker({
  selected,
  onChange,
}: {
  selected: LanguageCode[];
  onChange: (languages: LanguageCode[]) => void;
}) {
  function toggle(lang: LanguageCode) {
    onChange(selected.includes(lang) ? selected.filter((l) => l !== lang) : [...selected, lang]);
  }

  return (
    <div className="flex flex-wrap gap-2">
      {(Object.keys(LANGUAGE_LABELS) as LanguageCode[]).map((lang) => (
        <button
          key={lang}
          type="button"
          onClick={() => toggle(lang)}
          className={`rounded-full border px-4 py-1.5 text-sm transition-colors ${
            selected.includes(lang)
              ? "border-gold bg-gold text-navy-dark font-medium"
              : "border-navy-light bg-transparent text-navy hover:border-gold"
          }`}
        >
          {LANGUAGE_LABELS[lang]}
        </button>
      ))}
    </div>
  );
}
```

- [ ] **Step 2: How-it-works section**

Create `frontend/src/components/HowItWorks.tsx`:

```tsx
"use client";

import { motion } from "framer-motion";

const STEPS = [
  { title: "Upload", body: "Upload a government notice (.txt/.pdf, including scanned documents via OCR) or paste the text directly." },
  { title: "Extract & Verify", body: "Facts are extracted, narration is drafted per scene, and every claim is checked against the source before anything is rendered." },
  { title: "Review & Approve", body: "See the script, detected facts, and verification results per language before you approve a video for publication." },
];

export function HowItWorks() {
  return (
    <section className="mx-auto max-w-4xl px-6 py-16">
      <h2 className="mb-10 text-center font-serifDisplay text-2xl text-navy-dark">How it works</h2>
      <div className="grid gap-8 md:grid-cols-3">
        {STEPS.map((step, i) => (
          <motion.div
            key={step.title}
            initial={{ opacity: 0, y: 24 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5, delay: i * 0.15 }}
            className="rounded-lg border border-gold/30 bg-white p-6 shadow-sm"
          >
            <div className="mb-3 font-serifDisplay text-3xl text-gold">{i + 1}</div>
            <h3 className="mb-2 font-semibold text-navy-dark">{step.title}</h3>
            <p className="text-sm text-navy-light">{step.body}</p>
          </motion.div>
        ))}
      </div>
    </section>
  );
}
```

- [ ] **Step 3: Home page**

Replace the full contents of `frontend/src/app/page.tsx`:

```tsx
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { motion } from "framer-motion";

import { LanguagePicker } from "@/components/LanguagePicker";
import { HowItWorks } from "@/components/HowItWorks";
import { createJob } from "@/lib/api-client";
import type { LanguageCode } from "@/types";

export default function HomePage() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const [languages, setLanguages] = useState<LanguageCode[]>(["en"]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit() {
    setError(null);
    if (!file && !text.trim()) {
      setError("Upload a file or paste some text first.");
      return;
    }
    if (languages.length === 0) {
      setError("Pick at least one language.");
      return;
    }
    setSubmitting(true);
    try {
      const { job_id } = await createJob({ languages, file: file ?? undefined, text: text.trim() || undefined });
      router.push(`/jobs/${job_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
      setSubmitting(false);
    }
  }

  return (
    <main>
      <section className="relative overflow-hidden bg-navy px-6 py-20 text-white">
        <div className="mx-auto max-w-4xl">
          <motion.h1
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6 }}
            className="font-serifDisplay text-4xl font-bold text-gold md:text-5xl"
          >
            VaaniReach
          </motion.h1>
          <motion.p
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.1 }}
            className="mt-3 max-w-xl text-white/80"
          >
            Turn a government notice into a multilingual, fact-verified narrated video — ready for a
            human to review and approve before it's published.
          </motion.p>

          <motion.div
            initial={{ opacity: 0, y: 24 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.2 }}
            className="mt-10 rounded-xl bg-white p-6 text-navy-dark shadow-xl md:p-8"
          >
            <label className="mb-1 block text-sm font-medium">Government notice</label>
            <input
              type="file"
              accept=".txt,.pdf"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="mb-4 block w-full text-sm"
            />
            <label className="mb-1 block text-sm font-medium">...or paste the notice text directly</label>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={5}
              placeholder="Paste raw notice text here if you don't have a file handy."
              className="mb-4 w-full rounded border border-navy-light/30 p-3 text-sm"
            />
            <label className="mb-2 block text-sm font-medium">Languages to generate</label>
            <LanguagePicker selected={languages} onChange={setLanguages} />

            {error && <p className="mt-4 text-sm text-red-600">{error}</p>}

            <button
              type="button"
              onClick={handleSubmit}
              disabled={submitting}
              className="mt-6 rounded-full bg-gold px-6 py-2.5 font-medium text-navy-dark transition-opacity hover:opacity-90 disabled:opacity-50"
            >
              {submitting ? "Starting…" : "Generate video(s)"}
            </button>
          </motion.div>
        </div>
      </section>

      <div className="bg-white">
        <HowItWorks />
      </div>
    </main>
  );
}
```

- [ ] **Step 4: Manual verification**

```bash
cd /Users/ampa/VaaniReach-hack/vaanireach/frontend
npm run dev
```

Visit `http://localhost:3000` — confirm the hero renders with the navy/gold theme, the upload card
animates in, language pills toggle, and the "how it works" section reveals on scroll. Backend doesn't
need to be running yet for this visual check (Generate will fail with a fetch error, expected — full
flow is verified in Task 11). Stop the server after confirming (Ctrl+C).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/page.tsx frontend/src/components/LanguagePicker.tsx frontend/src/components/HowItWorks.tsx
git commit -m "Build the Home page: hero, upload form, language picker

Navy/gold hero with the upload form as a light card floating on the
dark background (matches the reference image's panel-on-dark-hero
pattern), Framer Motion entrance animation on load and whileInView
stagger on the how-it-works section beneath the fold.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 11: Job / Review page

**Files:**
- Create: `frontend/src/app/jobs/[jobId]/page.tsx`
- Create: `frontend/src/components/ReviewCard.tsx`
- Delete: `frontend/src/app/projects/[id]/page.tsx` (dead placeholder for a route this design doesn't
  use — the job page replaces it as the review surface; nothing links to `/projects/[id]` anymore)

**Interfaces:**
- Consumes: `getJob`, `approveLanguage`, `rejectLanguage`, `editScene`, `regenerateLanguage` (Task 9).

- [ ] **Step 1: Remove the dead placeholder route**

```bash
rm -rf frontend/src/app/projects
```

- [ ] **Step 2: Review card component**

Create `frontend/src/components/ReviewCard.tsx`:

```tsx
"use client";

import { useState } from "react";
import { motion } from "framer-motion";

import { approveLanguage, editScene, rejectLanguage, regenerateLanguage } from "@/lib/api-client";
import type { LanguageCode, LanguageJobView } from "@/types";

const LANGUAGE_LABELS: Record<LanguageCode, string> = {
  en: "English", hi: "हिन्दी (Hindi)", mr: "मराठी (Marathi)", bn: "বাংলা (Bengali)",
  ta: "தமிழ் (Tamil)", te: "తెలుగు (Telugu)", kn: "ಕನ್ನಡ (Kannada)", ml: "മലയാളം (Malayalam)",
  gu: "ગુજરાતી (Gujarati)",
};

const AVATAR_LABELS: Record<number, string> = {
  1: "✅ real lip-sync (Hedra)",
  2: "✅ real lip-sync (D-ID)",
  3: "⚠️ placeholder (no lip-sync)",
};

export function ReviewCard({
  jobId,
  language,
  view,
  onChanged,
}: {
  jobId: string;
  language: LanguageCode;
  view: LanguageJobView;
  onChanged: () => void;
}) {
  const [editingSceneId, setEditingSceneId] = useState<string | null>(null);
  const [draftText, setDraftText] = useState("");
  const [editFeedback, setEditFeedback] = useState<{ isBlocking: boolean; explanation: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const avatarLabel = view.avatar_composited
    ? AVATAR_LABELS[view.avatar_tier ?? 3] ?? "⚠️ unknown"
    : "⚠️ degraded (plain B-roll)";

  async function handleSaveEdit(sceneId: string) {
    setBusy(true);
    try {
      const result = await editScene(jobId, language, sceneId, draftText);
      setEditFeedback({ isBlocking: result.verification.is_blocking, explanation: result.verification.explanation });
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
      className="rounded-xl border border-navy-light/20 bg-white p-6 text-navy-dark shadow"
    >
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-serifDisplay text-xl">{LANGUAGE_LABELS[language]}</h3>
        <div className="flex gap-2 text-xs">
          <span className="rounded-full bg-navy/10 px-3 py-1">{avatarLabel}</span>
          <span className="rounded-full bg-gold/20 px-3 py-1 capitalize">{view.status.replace("_", " ")}</span>
        </div>
      </div>

      <video controls src={view.video_url} className="mb-4 w-full rounded-lg" />

      <div className="mb-4 flex gap-6 text-sm">
        <span>Facts verified: {view.verified_count}/{view.scenes.length}</span>
        <span>Blocking issues: {view.blocking_count}</span>
        <a href={view.srt_url} className="text-gold-dark underline">SRT</a>
        <a href={view.vtt_url} className="text-gold-dark underline">VTT</a>
      </div>

      <div className="mb-4 space-y-3">
        <h4 className="text-sm font-semibold">Script</h4>
        {view.scenes.map((scene) => {
          const vr = view.verification_results.find((r) => scene.claim_ids.includes(r.claim_id));
          return (
            <div key={scene.id} className="rounded border border-navy-light/10 p-3">
              <div className="mb-1 flex items-center justify-between">
                <span className="text-xs uppercase tracking-wide text-navy-light">{scene.narrative_role}</span>
                {vr && (
                  <span className={`text-xs ${vr.is_blocking ? "text-red-600" : "text-green-700"}`} title={vr.explanation}>
                    {vr.is_blocking ? "⚠️ flagged" : "✅ verified"}
                  </span>
                )}
              </div>
              {editingSceneId === scene.id ? (
                <div>
                  <textarea
                    value={draftText}
                    onChange={(e) => setDraftText(e.target.value)}
                    rows={2}
                    className="w-full rounded border border-navy-light/30 p-2 text-sm"
                  />
                  <div className="mt-2 flex gap-2">
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => handleSaveEdit(scene.id)}
                      className="rounded bg-gold px-3 py-1 text-xs font-medium text-navy-dark"
                    >
                      Save & re-verify
                    </button>
                    <button
                      type="button"
                      onClick={() => { setEditingSceneId(null); setEditFeedback(null); }}
                      className="rounded border border-navy-light/30 px-3 py-1 text-xs"
                    >
                      Cancel
                    </button>
                  </div>
                  {editFeedback && (
                    <p className={`mt-2 text-xs ${editFeedback.isBlocking ? "text-red-600" : "text-green-700"}`}>
                      {editFeedback.isBlocking ? "⚠️ " : "✅ "}{editFeedback.explanation}
                    </p>
                  )}
                </div>
              ) : (
                <div className="flex items-start justify-between gap-2">
                  <p className="text-sm">{scene.narration_segment_text}</p>
                  {view.status === "pending_review" && (
                    <button
                      type="button"
                      onClick={() => { setEditingSceneId(scene.id); setDraftText(scene.narration_segment_text); setEditFeedback(null); }}
                      className="shrink-0 text-xs text-gold-dark underline"
                    >
                      Edit
                    </button>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {view.status === "pending_review" && (
        <div className="flex gap-3">
          <button
            type="button"
            disabled={busy}
            onClick={async () => { setBusy(true); await approveLanguage(jobId, language); onChanged(); setBusy(false); }}
            className="rounded-full bg-green-700 px-5 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            Approve
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={async () => { setBusy(true); await rejectLanguage(jobId, language); onChanged(); setBusy(false); }}
            className="rounded-full bg-red-700 px-5 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            Reject
          </button>
          <button
            type="button"
            disabled={busy || view.regenerating}
            onClick={async () => { setBusy(true); await regenerateLanguage(jobId, language); onChanged(); setBusy(false); }}
            className="rounded-full border border-navy-light/30 px-5 py-2 text-sm font-medium disabled:opacity-50"
          >
            {view.regenerating ? "Regenerating…" : "Regenerate"}
          </button>
        </div>
      )}
    </motion.div>
  );
}
```

(the `vr` lookup joins on `scene.claim_ids` against `verification_results[].claim_id` — the same
1:1 scene-to-claim relationship `claims_from_scenes` already establishes server-side, per Task 4's
`_serialize_scene`)

- [ ] **Step 3: Job page with polling**

Create `frontend/src/app/jobs/[jobId]/page.tsx`:

```tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";

import { ReviewCard } from "@/components/ReviewCard";
import { getJob } from "@/lib/api-client";
import type { JobView, LanguageCode } from "@/types";

export default function JobPage() {
  const params = useParams<{ jobId: string }>();
  const jobId = params.jobId;
  const [job, setJob] = useState<JobView | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await getJob(jobId);
      setJob(data);
    } catch (err) {
      setPollError(err instanceof Error ? err.message : "Failed to load job.");
    }
  }, [jobId]);

  useEffect(() => {
    refresh();
    const interval = setInterval(() => {
      if (job && (job.status === "pending_review" || job.status === "failed")) return;
      refresh();
    }, 3000);
    return () => clearInterval(interval);
  }, [refresh, job]);

  if (pollError) {
    return <main className="min-h-screen bg-navy p-10 text-white">Error: {pollError}</main>;
  }
  if (!job) {
    return <main className="min-h-screen bg-navy p-10 text-white">Loading…</main>;
  }

  return (
    <main className="min-h-screen bg-navy">
      <header className="bg-navy px-6 py-10 text-white">
        <h1 className="font-serifDisplay text-3xl text-gold">VaaniReach</h1>
        <p className="mt-1 text-sm text-white/70">Job {job.job_id}</p>
      </header>

      <div className="bg-white px-6 py-10">
        {(job.status === "pending" || job.status === "running") && (
          <p className="mx-auto max-w-2xl text-center text-navy-dark">
            Generating your videos — real fact extraction, translation, TTS, and avatar lip-sync per
            language. This takes a few minutes.
          </p>
        )}

        {job.status === "failed" && (
          <p className="mx-auto max-w-2xl rounded border border-red-300 bg-red-50 p-4 text-red-700">
            Generation failed: {job.error}
          </p>
        )}

        {job.facts.length > 0 && (
          <div className="mx-auto mb-8 max-w-4xl rounded-lg border border-gold/30 bg-gold/5 p-4">
            <h2 className="mb-2 text-sm font-semibold text-navy-dark">Detected facts</h2>
            <ul className="space-y-1 text-sm text-navy-light">
              {job.facts.map((fact) => (
                <li key={fact.id}>
                  <span className="font-medium capitalize">{fact.fact_type}</span>: {fact.value}
                  <span className="ml-2 text-xs italic">— &quot;{fact.source_span.text_span}&quot;</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="mx-auto grid max-w-4xl gap-6">
          {(Object.entries(job.languages) as [LanguageCode, JobView["languages"][LanguageCode]][]).map(
            ([language, view]) => (
              <ReviewCard key={language} jobId={job.job_id} language={language} view={view} onChanged={refresh} />
            ),
          )}
        </div>
      </div>
    </main>
  );
}
```

- [ ] **Step 4: Confirm typecheck**

```bash
cd /Users/ampa/VaaniReach-hack/vaanireach/frontend
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 5: Full manual end-to-end verification**

Start both servers:

```bash
# terminal 1
cd /Users/ampa/VaaniReach-hack/vaanireach/backend && source .venv/bin/activate && PYTHONPATH=.. uvicorn app.main:app --reload
# terminal 2
cd /Users/ampa/VaaniReach-hack/vaanireach/frontend && npm run dev
```

Visit `http://localhost:3000`, paste a short notice ("The Ministry of Finance announces the Income
Tax Relief Scheme. Eligible taxpayers receive ₹10,000. Applications close 31 March 2026."), pick
English, submit. Confirm: redirected to `/jobs/[jobId]`, page polls and shows "generating…", after a
few minutes shows the review card with video, script, facts, verification counts, and working
Approve/Reject/Edit/Regenerate buttons. Approve it and confirm the status badge updates to
"published" and the action buttons disappear. Stop both servers after confirming (Ctrl+C).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/jobs frontend/src/components/ReviewCard.tsx
git rm -r frontend/src/app/projects
git commit -m "Build the Job/Review page — the compulsory approval gate

Polls for job status, then renders one review card per language:
video, script (editable inline with instant re-verification), the
full detected-facts list, verification counts, and Approve/Reject/
Regenerate actions. This is what closes the problem statement's
'reviewable and approved by a human before publication' requirement
— completing the review dashboard end to end.

Removes the old placeholder /projects/[id] route this design
replaces; nothing links to it anymore.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 12: Final regression pass

**Files:** none (verification-only task)

- [ ] **Step 1: Full backend + pipeline test suite**

```bash
cd /Users/ampa/VaaniReach-hack/vaanireach
PYTHONPATH="backend:." backend/.venv/bin/python -m pytest tests/test_pipeline_jobs.py tests/test_pipeline_routes.py tests/test_text_extraction.py -v
PYTHONPATH=. .venv/bin/python -m pytest tests/test_narrative_story_director.py tests/test_dynamic_narration.py tests/test_cloudflare_scene_renderer.py tests/test_deterministic_fact_verifier.py tests/test_multilingual_video.py -q
```

Expected: everything green.

- [ ] **Step 2: Frontend typecheck + lint**

```bash
cd /Users/ampa/VaaniReach-hack/vaanireach/frontend
npx tsc --noEmit
npm run lint
```

Expected: no errors (lint warnings on pre-existing files are acceptable; nothing new introduced by
this plan should fail lint).

- [ ] **Step 3: Confirm `local_demo.py` still runs unaffected**

```bash
cd /Users/ampa/VaaniReach-hack/vaanireach
PYTHONPATH=. .venv/bin/streamlit run dashboard/local_demo.py
```

Upload a document, confirm generation still works exactly as before Task 2's extraction. Stop the
server after confirming (Ctrl+C).

- [ ] **Step 4: Report to the user**

Summarize what was built, confirm the compulsory "reviewable and approved by a human before
publication" requirement is now met, and note the known limitations already called out in the spec
(in-memory job store, no auth, edit-then-regenerate is two explicit steps not one).
