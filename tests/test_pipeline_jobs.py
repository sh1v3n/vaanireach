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
