"""HedraAvatarManager (v3 client): unit-level checks that don't need
network access — the API contract itself (v3 base URL, /files upload,
/models/{model} submit, /jobs/{id}/status + /jobs/{id} poll/download) was
verified live against the real Hedra API during a Task 9 end-to-end
investigation (2026-08-20): file uploads succeeded and a real generation
job was accepted through request validation, rejected only for
INSUFFICIENT_BALANCE (402) — see providers/video/hedra_client.py's
module docstring for the full contract this client implements.

Finding #2 (final whole-branch review): the tests below additionally
exercise the actual request flow the v3 rewrite changed the shape of —
_upload_file, _attempt, _poll_and_download, _download_result — via a
fake requests.Session (no real network calls, no API key required) so
this file passes with zero network access and zero .env configuration.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import providers.video.hedra_client as hedra_client  # noqa: E402
from providers.video.hedra_client import (  # noqa: E402
    HEDRA_BASE_URL,
    DEFAULT_MODEL,
    HedraAvatarManager,
    HedraGenerationFailedError,
    HedraGenerationTimeoutError,
    _HedraHTTPError,
    _classify_error,
)


def test_base_url_targets_v3_not_the_old_web_app_public_endpoint():
    """Regression guard for the actual bug this client was rewritten to
    fix: the old `https://api.hedra.com/web-app/public` base URL rejects
    every v3 API key with a 403 explaining the account has moved to v3."""
    assert HEDRA_BASE_URL == "https://api.hedra.com/v3"
    assert "web-app" not in HEDRA_BASE_URL


def test_default_model_is_the_longform_audio_to_video_model():
    assert DEFAULT_MODEL == "hedra-avatar"


def test_classify_error_treats_insufficient_balance_as_a_client_error():
    """Regression guard for the real 402 INSUFFICIENT_BALANCE response
    reproduced live: since the API wallet is shared across every key on
    an account, a balance failure on one key fails identically on every
    other key too — this must raise immediately (client_error), not burn
    through the whole key pool first."""
    exc = _HedraHTTPError(402, "Your API wallet balance is $0.00.")
    assert _classify_error(exc) == "client_error"


def test_classify_error_still_treats_auth_and_rate_limit_correctly():
    assert _classify_error(_HedraHTTPError(401, "unauthorized")) == "auth"
    assert _classify_error(_HedraHTTPError(403, "forbidden")) == "auth"
    assert _classify_error(_HedraHTTPError(429, "rate limited")) == "rate_limit"
    assert _classify_error(_HedraHTTPError(400, "bad request")) == "client_error"
    assert _classify_error(_HedraHTTPError(500, "server error")) == "transient"


def test_manager_construction_requires_no_stale_v2_model_id_kwargs():
    """Regression guard: the old client's __init__ accepted model_id=/
    model_slug= (v2-era, meaningless on v3, where the model is chosen by
    URL path, not a body field). The v3 client's constructor takes model=
    instead — passing the old kwarg names must fail loudly (TypeError),
    not be silently accepted and ignored."""
    import pytest
    with pytest.raises(TypeError):
        HedraAvatarManager(api_keys=["fake-key"], model_id="some-old-id")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Fake requests.Session/Response plumbing (no unittest.mock, no real network)
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Stands in for a requests.Response — only the surface this client
    actually touches (.status_code, .json(), .content, .text,
    .raise_for_status())."""

    def __init__(self, status_code: int = 200, json_data: dict | None = None, content: bytes = b"") -> None:
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}
        self.content = content
        self.text = str(self._json_data)

    def json(self) -> dict:
        return self._json_data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"fake response raised for status {self.status_code}")


class _FakeSession:
    """A minimal fake matching requests.Session's .post()/.get() surface.
    Responses are queued in call order (the client's own code issues its
    POST/GET calls in a fixed, deterministic sequence per attempt), and
    every call is recorded for the test to inspect afterwards."""

    def __init__(self, post_responses: list[_FakeResponse] | None = None, get_responses: list[_FakeResponse] | None = None) -> None:
        self._post_responses = list(post_responses or [])
        self._get_responses = list(get_responses or [])
        self.post_calls: list[tuple[str, dict]] = []
        self.get_calls: list[tuple[str, dict]] = []

    def post(self, url: str, **kwargs) -> _FakeResponse:
        self.post_calls.append((url, kwargs))
        if not self._post_responses:
            raise AssertionError(f"_FakeSession.post: no queued response left for {url}")
        return self._post_responses.pop(0)

    def get(self, url: str, **kwargs) -> _FakeResponse:
        self.get_calls.append((url, kwargs))
        if not self._get_responses:
            raise AssertionError(f"_FakeSession.get: no queued response left for {url}")
        return self._get_responses.pop(0)


class _AlwaysInProgressSession:
    """A GET-only fake that reports IN_PROGRESS forever — used to drive
    the poll loop past its deadline without ever completing, to exercise
    the real timeout path."""

    def __init__(self) -> None:
        self.get_calls = 0

    def get(self, url: str, **kwargs) -> _FakeResponse:
        self.get_calls += 1
        return _FakeResponse(200, {"status": "IN_PROGRESS", "progress": 0.1})


def _make_local_file(tmp_path: Path, name: str) -> str:
    path = tmp_path / name
    path.write_bytes(b"fake-bytes-not-a-real-media-file")
    return str(path)


def test_generate_avatar_video_full_successful_flow_returns_the_downloaded_bytes(tmp_path, monkeypatch):
    """End to end through generate_avatar_video(): two /files uploads,
    a 202 job submission, one COMPLETED poll, then the outputs[0].url
    download — asserts the exact bytes returned come from that download,
    not from anywhere else in the pipeline."""
    image_path = _make_local_file(tmp_path, "portrait.jpg")
    audio_path = _make_local_file(tmp_path, "narration.wav")

    fake_session = _FakeSession(
        post_responses=[
            _FakeResponse(200, {"url": "https://hedra-files.example/image.jpg?sig=abc"}),
            _FakeResponse(200, {"url": "https://hedra-files.example/audio.wav?sig=def"}),
            _FakeResponse(202, {"job_id": "job-123", "status": "queued"}),
        ],
        get_responses=[
            _FakeResponse(200, {"job_id": "job-123", "status": "COMPLETED", "progress": 1.0}),
            _FakeResponse(200, {"job_id": "job-123", "outputs": [{"url": "https://hedra-cdn.example/result.mp4"}]}),
        ],
    )

    real_video_bytes = b"FAKE-MP4-BYTES-1234567890"
    monkeypatch.setattr(
        hedra_client.requests, "get",
        lambda url, timeout=None: _FakeResponse(200, content=real_video_bytes),
    )

    manager = HedraAvatarManager(api_keys=["fake-key-1"], session=fake_session)
    result = manager.generate_avatar_video(image_path, audio_path, text_prompt="hello world")

    assert result == real_video_bytes
    assert len(fake_session.post_calls) == 3
    assert len(fake_session.get_calls) == 2


def test_generate_avatar_video_submits_the_v3_input_body_shape_the_real_api_requires(tmp_path, monkeypatch):
    """The real v3 API requires input.prompt/aspect_ratio/resolution and
    the nested {"source": "url", "url": ...} shape for both start_image
    and audio — inspects what the fake session actually received on the
    POST /models/{model} call, not just that the call succeeded."""
    image_path = _make_local_file(tmp_path, "portrait.jpg")
    audio_path = _make_local_file(tmp_path, "narration.wav")

    fake_session = _FakeSession(
        post_responses=[
            _FakeResponse(200, {"url": "https://hedra-files.example/image.jpg"}),
            _FakeResponse(200, {"url": "https://hedra-files.example/audio.wav"}),
            _FakeResponse(202, {"job_id": "job-456"}),
        ],
        get_responses=[
            _FakeResponse(200, {"status": "COMPLETED"}),
            _FakeResponse(200, {"outputs": [{"url": "https://hedra-cdn.example/result.mp4"}]}),
        ],
    )
    monkeypatch.setattr(
        hedra_client.requests, "get",
        lambda url, timeout=None: _FakeResponse(200, content=b"bytes"),
    )

    manager = HedraAvatarManager(api_keys=["fake-key-1"], session=fake_session, model="hedra-avatar")
    manager.generate_avatar_video(
        image_path, audio_path, text_prompt="A person speaking",
        aspect_ratio="9:16", resolution="540p",
    )

    submit_url, submit_kwargs = fake_session.post_calls[2]
    assert submit_url == f"{HEDRA_BASE_URL}/models/hedra-avatar"
    body = submit_kwargs["json"]
    assert "input" in body
    input_body = body["input"]
    assert input_body["prompt"] == "A person speaking"
    assert input_body["aspect_ratio"] == "9:16"
    assert input_body["resolution"] == "540p"
    assert input_body["start_image"] == {"source": "url", "url": "https://hedra-files.example/image.jpg"}
    assert input_body["audio"] == {"source": "url", "url": "https://hedra-files.example/audio.wav"}


def test_poll_and_download_raises_generation_failed_on_status_failed():
    """Exercises the real _poll_and_download code path directly (the
    line that changed shape in the v3 rewrite: status field polling +
    the FAILED terminal-status check) — a FAILED job must raise
    HedraGenerationFailedError."""
    fake_session = _FakeSession(get_responses=[_FakeResponse(200, {"status": "FAILED"})])
    manager = HedraAvatarManager(api_keys=["fake-key-1"], session=fake_session)

    try:
        manager._poll_and_download("fake-key-1", "job-789")
        raise AssertionError("expected HedraGenerationFailedError")
    except HedraGenerationFailedError:
        pass


def test_poll_and_download_raises_timeout_when_stuck_in_progress(monkeypatch):
    """Exercises the real _poll_and_download timeout path without a real
    300s wait: overrides the module-level POLL_TIMEOUT_SECONDS/
    POLL_INTERVAL_SECONDS constants to a tiny test-only window, and drives
    the poll loop with a session that reports IN_PROGRESS forever."""
    monkeypatch.setattr(hedra_client, "POLL_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(hedra_client, "POLL_INTERVAL_SECONDS", 0.01)

    fake_session = _AlwaysInProgressSession()
    manager = HedraAvatarManager(api_keys=["fake-key-1"], session=fake_session)

    try:
        manager._poll_and_download("fake-key-1", "job-stuck")
        raise AssertionError("expected HedraGenerationTimeoutError")
    except HedraGenerationTimeoutError:
        pass

    assert fake_session.get_calls >= 1
