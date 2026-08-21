"""pipeline — the real job API this backend was missing. Wraps the
existing, tested rendering.multilingual_video.run_full_pipeline /
generate_language_video exactly as they already work; see
docs/superpowers/specs/2026-08-21-review-dashboard-frontend-design.md
for the full design and rationale. Every other route module in this
package (documents.py, facts.py, generate.py, ...) is a separate,
unrelated 501-stub architecture — untouched, unaffected by this file.
"""
from __future__ import annotations

import logging
import threading
import traceback

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from app.pipeline_jobs import JobRecord, JobStore, LanguageJobState
from core.models.claim import Claim
from core.models.enums import Criticality, LanguageCode
from providers.documents.text_extraction import extract_text_from_upload_bytes
from providers.narrative.template_story_director import TemplateStoryDirector
from providers.translation.groq_translation_provider import GroqTranslationProvider
from providers.verification.deterministic_fact_verifier import DeterministicFactVerifier
from rendering.multilingual_video import generate_language_video, run_full_pipeline

logger = logging.getLogger("vaanireach.backend.app.routes.pipeline")

router = APIRouter(prefix="/pipeline", tags=["pipeline"])
job_store = JobStore()


class CreateJobResponse(BaseModel):
    job_id: str
    status: str


def _on_stage(record: JobRecord, stage_name: str, data: dict) -> None:
    """run_full_pipeline calls this synchronously on the same background
    thread as _run_generation, one stage at a time — never concurrently
    — so acquiring record.lock here is always safe (never contends with
    itself)."""
    with record.lock:
        record.stage = stage_name
        if stage_name == "facts_extracted":
            record.facts = data["facts"]


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
        results = run_full_pipeline(
            document_text, languages=languages, project_id=record.job_id,
            on_stage=lambda name, data: _on_stage(record, name, data),
        )
    except Exception as exc:  # noqa: BLE001 - must never crash the background thread silently
        with record.lock:
            record.status = "failed"
            record.error = f"{exc}\n{traceback.format_exc()}"
        return

    with record.lock:
        for result in results:
            record.languages[result.language] = LanguageJobState(status="pending_review", result=result)
        record.status = "pending_review"
        record.stage = None


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
        "error": state.error,
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
        # record.facts is populated as soon as extraction succeeds (the
        # "facts_extracted" stage) — available well before any language
        # finishes, so the review page can show what was found in the
        # document immediately. Falls back to a completed language's own
        # facts (identical data) for jobs from before this field existed.
        facts_source = record.facts or (
            next(iter(record.languages.values())).result.facts if record.languages else []
        )
        return {
            "job_id": record.job_id,
            "status": record.status,
            "stage": record.stage,
            "error": record.error,
            "languages": languages_payload,
            "facts": [_serialize_fact(f) for f in facts_source],
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
    if state.regenerating:
        raise HTTPException(status_code=409, detail="Language is currently regenerating.")
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


class EditSceneRequest(BaseModel):
    scene_id: str
    narration_segment_text: str


@router.post("/jobs/{job_id}/languages/{language}/edit")
async def edit_scene(job_id: str, language: LanguageCode, payload: EditSceneRequest) -> dict:
    record = job_store.get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    with record.lock:
        state = _get_pending_review_language_state(record, language)

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
    except Exception as exc:  # noqa: BLE001 - a failed regenerate must never crash the thread or corrupt state
        logger.exception("_run_regenerate: regenerate failed for language=%s (job=%s)", language, record.job_id)
        with record.lock:
            record.languages[language].regenerating = False
            record.languages[language].error = str(exc)
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
        state.regenerating = True

    thread = threading.Thread(target=_run_regenerate, args=(record, language), daemon=True)
    thread.start()
    return {"status": "regenerating"}
