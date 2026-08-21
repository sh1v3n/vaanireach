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


def test_approve_returns_409_while_the_language_is_regenerating(client):
    """Closes a race: a Regenerate in flight must block Approve/Reject,
    otherwise an approval landing mid-regenerate gets silently
    overwritten (un-published with no error) the instant the
    background thread finishes and replaces the LanguageJobState."""
    job = _create_job_and_wait(client)
    job_id = job["job_id"]

    from app.routes.pipeline import job_store

    record = job_store.get_job(job_id)
    with record.lock:
        record.languages[LanguageCode.EN].regenerating = True

    resp = client.post(f"/pipeline/jobs/{job_id}/languages/en/approve")
    assert resp.status_code == 409


def test_on_stage_helper_sets_stage_and_captures_facts_on_facts_extracted():
    from app.pipeline_jobs import JobRecord
    from app.routes.pipeline import _on_stage

    record = JobRecord(job_id="test-stage")
    facts = [SourceFact(
        project_id="test-stage", document_id="doc-1", fact_type=FactType.AMOUNT, value="₹10,000",
        raw_text="₹10,000", source_span=SourceSpan(document_id="doc-1", page_number=1, text_span="₹10,000"),
        criticality=Criticality.CRITICAL, confidence=0.95, extractor_name="fake",
    )]

    _on_stage(record, "extracting_facts", {})
    assert record.stage == "extracting_facts"
    assert record.facts == []  # not this stage's job to set facts

    _on_stage(record, "facts_extracted", {"facts": facts})
    assert record.stage == "facts_extracted"
    assert record.facts == facts

    _on_stage(record, "rendering_images", {})
    assert record.stage == "rendering_images"
    assert record.facts == facts  # untouched by a later, unrelated stage


def test_job_facts_are_visible_immediately_after_extraction_even_if_generation_later_fails(client, monkeypatch):
    """Realistic scenario: extraction genuinely succeeds (real facts
    found) but something downstream fails (Groq/Cloudflare/Sarvam
    outage, etc.) — the officer should still see what was found in the
    document, not a blank page, even though the job ends up 'failed'."""
    facts = [SourceFact(
        project_id="test-partial", document_id="doc-1", fact_type=FactType.DEADLINE, value="31 March 2026",
        raw_text="31 March 2026", source_span=SourceSpan(document_id="doc-1", page_number=1, text_span="31 March 2026"),
        criticality=Criticality.CRITICAL, confidence=0.95, extractor_name="fake",
    )]

    def failing_pipeline_after_extraction(text, *, languages, project_id, on_stage=None, **kwargs):
        if on_stage is not None:
            on_stage("extracting_facts", {})
            on_stage("facts_extracted", {"facts": facts})
        raise RuntimeError("simulated downstream failure after extraction")

    monkeypatch.setattr("app.routes.pipeline.run_full_pipeline", failing_pipeline_after_extraction)

    resp = client.post("/pipeline/jobs", data={"languages": ["en"], "text": "test notice text"})
    job_id = resp.json()["job_id"]

    for _ in range(50):
        job = client.get(f"/pipeline/jobs/{job_id}").json()
        if job["status"] == "failed":
            break
        time.sleep(0.05)
    else:
        raise AssertionError("job never reached failed status")

    assert len(job["facts"]) == 1
    assert job["facts"][0]["value"] == "31 March 2026"
    assert "simulated downstream failure" in job["error"]


def test_get_job_response_includes_stage_field(client):
    job = _create_job_and_wait(client)
    # once generation completes, stage is cleared back to None — the
    # per-language status carries the real state instead
    assert job["stage"] is None
