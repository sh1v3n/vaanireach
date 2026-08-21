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
from core.models.fact import SourceFact
from providers.narrative.dynamic_narration import NarrationStyle
from providers.tts.sarvam_voices import DEFAULT_SPEAKER
from rendering.multilingual_video import LanguageVideoResult

JobStatus = Literal["pending", "running", "pending_review", "failed"]
LanguageStatus = Literal["pending_review", "published", "rejected"]


@dataclass
class LanguageJobState:
    status: LanguageStatus
    result: LanguageVideoResult
    regenerating: bool = False
    error: str | None = None


@dataclass
class JobRecord:
    job_id: str
    status: JobStatus = "pending"
    stage: str | None = None
    """The pipeline's current step (see run_full_pipeline's on_stage
    docstring for the exact stage names) — set as generation progresses,
    well before any language finishes. None before generation starts and
    once the job reaches a terminal status (pending_review/failed), where
    per-language status carries the real state instead."""
    error: str | None = None
    facts: list[SourceFact] = field(default_factory=list)
    """Populated as soon as extraction succeeds (the "facts_extracted"
    stage) — independent of any language's generation finishing, so the
    review page can show what was found in the document immediately
    rather than waiting minutes for the first video."""
    speaker: str = DEFAULT_SPEAKER
    style: NarrationStyle = "news"
    pace: float = 1.0
    """Always a resolved, concrete value (never None) — the route
    handler resolves a style-appropriate default at job creation, per
    run_full_pipeline's own pace-resolution logic, so Regenerate (which
    calls generate_language_video directly, bypassing that resolution)
    always has a real number to reuse."""
    pitch: float | None = None
    """Whole-job voice settings (speaker/style/pace/pitch), set once at
    job creation and never changed afterward — Regenerate reuses these
    exact values so a re-rendered language keeps the officer's original
    voice choice instead of silently reverting to the pipeline's own
    defaults."""
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
