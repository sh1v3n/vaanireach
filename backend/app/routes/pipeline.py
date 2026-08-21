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
from fastapi.responses import FileResponse, PlainTextResponse
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
    # Capture status before starting the thread, not after: Thread.start()
    # internally waits for the new thread to begin running (releasing the
    # GIL to do so), so for a fast-finishing background job the thread can
    # already be done by the time start() returns control here — reading
    # record.status afterwards would race with it.
    initial_status = record.status
    thread = threading.Thread(target=_run_generation, args=(record, document_text, languages), daemon=True)
    thread.start()
    return CreateJobResponse(job_id=record.job_id, status=initial_status)


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
