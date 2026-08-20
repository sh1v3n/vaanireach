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
