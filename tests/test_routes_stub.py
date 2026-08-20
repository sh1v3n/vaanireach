"""Every declared endpoint (except /health) must return 501 with the
NotImplementedResponse shape in Phase 0 — this is the contract, not a bug."""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_ok(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def _assert_not_implemented(resp) -> None:
    assert resp.status_code == 501
    body = resp.json()
    assert "detail" in body and "stage" in body


def test_create_project_stub(client: TestClient) -> None:
    resp = client.post("/projects", json={"name": "Test Project"})
    _assert_not_implemented(resp)


def test_upload_document_stub(client: TestClient) -> None:
    resp = client.post(
        "/projects/p1/documents",
        files={"file": ("notice.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    _assert_not_implemented(resp)


def test_process_project_stub(client: TestClient) -> None:
    resp = client.post("/projects/p1/process", json={})
    _assert_not_implemented(resp)


def test_list_facts_stub(client: TestClient) -> None:
    resp = client.get("/projects/p1/facts")
    _assert_not_implemented(resp)


def test_create_script_stub(client: TestClient) -> None:
    resp = client.post(
        "/projects/p1/scripts",
        json={"target_language": "hi", "audience": "general public", "desired_duration_seconds": 60},
    )
    _assert_not_implemented(resp)


def test_translate_script_stub(client: TestClient) -> None:
    resp = client.post(
        "/projects/p1/translate", json={"script_id": "s1", "target_languages": ["hi", "mr", "ta"]}
    )
    _assert_not_implemented(resp)


def test_create_storyboard_stub(client: TestClient) -> None:
    resp = client.post("/projects/p1/storyboard", json={"script_id": "s1", "language": "hi"})
    _assert_not_implemented(resp)


def test_generate_media_stub(client: TestClient) -> None:
    resp = client.post("/projects/p1/generate", json={"storyboard_id": "sb1"})
    _assert_not_implemented(resp)


def test_list_verification_results_stub(client: TestClient) -> None:
    resp = client.get("/projects/p1/verification")
    _assert_not_implemented(resp)


def test_get_workflow_trace_stub(client: TestClient) -> None:
    resp = client.get("/projects/p1/workflow")
    _assert_not_implemented(resp)


def test_approve_project_stub(client: TestClient) -> None:
    resp = client.post(
        "/projects/p1/approve", json={"video_asset_id": "v1", "decided_by": "officer-1"}
    )
    _assert_not_implemented(resp)


def test_reject_project_stub(client: TestClient) -> None:
    resp = client.post(
        "/projects/p1/reject", json={"video_asset_id": "v1", "decided_by": "officer-1"}
    )
    _assert_not_implemented(resp)
