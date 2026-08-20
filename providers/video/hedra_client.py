"""HedraAvatarManager — resilient multi-key wrapper around Hedra's
Public API v3 (`hedra-avatar` model): audio-to-video talking-avatar
generation.

Same key-rotation shape as GeminiManager/SarvamTTSManager (itertools.cycle
+ per-key cooldown on rate-limit/auth errors), but unlike a single text or
TTS call, one "attempt" here is a whole asynchronous pipeline — upload
image, upload audio, submit a generation job, poll until done — so
rotation happens once per FULL attempt: a failed/timed-out generation on
key 1 means "run the whole pipeline again on key 2", not "back off and
retry key 1".

API contract, verified live against the real v3 API during a Task 9
end-to-end run's failure investigation (2026-08-20): the previously used
`https://api.hedra.com/web-app/public` base URL is Hedra's OLD (pre-v3)
internal API — every configured key is a v3 key, which that endpoint
rejects with a 403 `PERMISSION_DENIED` explaining the account has moved
to Public API v3 (docs: https://api.hedra.com/v3/docs, spec:
https://api.hedra.com/v3/openapi.json). This client targets v3:

    Base URL: https://api.hedra.com/v3
    Auth:     X-API-Key: <key>  (confirmed unchanged from the old API —
              verified live: GET /v3/balance with X-API-Key returns 200)

    1. POST /files                 multipart file=<image bytes>
                                    -> {url, content_type, expires_at}
       POST /files                 multipart file=<audio bytes>
                                    -> {url, content_type, expires_at}
       (same generic upload endpoint for both — v3 has no separate
       per-media-type "asset" resource; the returned presigned `url` IS
       the file handle, valid for 1 hour, passed straight into the next
       call's `input.start_image`/`input.audio`.)
    2. POST /models/hedra-avatar   {input: {prompt, aspect_ratio,
                                    resolution, start_image:{source:"url",
                                    url}, audio:{source:"url", url},
                                    duration_ms?}}
                                    -> 202 {job_id, model, status,
                                    status_url, result_url}
       (the "hedra-avatar" model is Hedra's "latest longform avatar
       model, audio to video with full multi-language support... up to
       10 minutes long" per its own v3 catalog description — audio may
       be 0.5s-600s, images up to 10.4MB, audio up to 104.8MB, well
       within any narration length this pipeline produces.)
    3. GET  /jobs/{job_id}/status  (poll every ~5s) -> {job_id, status,
                                    progress}
       status in {IN_QUEUE, IN_PROGRESS, COMPLETED, FAILED}
    4. GET  /jobs/{job_id}         (once COMPLETED)  -> {..., outputs:
                                    [{status, asset_id, url,
                                    content_type, width, height,
                                    duration_ms}]}
    5. GET  outputs[0]['url']      (presigned, unauthenticated) -> mp4 bytes

Error envelope also changed for v3: `{"error": {"code", "message",
"retryable", ...}}` (was `{"code", "error_code", "messages": [...]}`).
"""
from __future__ import annotations

import itertools
import logging
import os
import time
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger("vaanireach.providers.hedra")

HEDRA_BASE_URL = "https://api.hedra.com/v3"
DEFAULT_MODEL = "hedra-avatar"

POLL_INTERVAL_SECONDS = 5.0
POLL_TIMEOUT_SECONDS = 120.0
REQUEST_TIMEOUT_SECONDS = 30.0
DOWNLOAD_TIMEOUT_SECONDS = 60.0
KEY_COOLDOWN_SECONDS = 60.0


class HedraAllKeysExhaustedError(RuntimeError):
    """Every key in the pool failed (rate-limit / auth / repeated
    transient error / generation failure / timeout) across one full
    rotation. The avatar provider catches this and fails over to D-ID."""


class HedraRequestError(RuntimeError):
    """A client-side request problem (400/404/422) — the same malformed
    request would fail identically on every key, so this is raised
    immediately without burning through the pool. The avatar provider
    still treats it as "Hedra tier failed" and falls over to D-ID."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"Hedra API rejected the request ({status_code}): {message}")
        self.status_code = status_code


class HedraGenerationTimeoutError(RuntimeError):
    """A generation job did not reach a terminal status within
    POLL_TIMEOUT_SECONDS — treated as a silent failure per spec, chained
    as the cause when the key pool is exhausted."""


class HedraGenerationFailedError(RuntimeError):
    """A generation job reached status=='FAILED' (or completed with no
    usable output url)."""


class _HedraHTTPError(RuntimeError):
    """Internal — every non-2xx response becomes one of these, then gets
    classified/re-raised as the right public exception type."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"Hedra API error {status_code}: {message}")
        self.status_code = status_code


def _load_keys_from_env(env_var: str = "HEDRA_API_KEYS") -> list[str]:
    raw = os.environ.get(env_var, "")
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        single = os.environ.get("HEDRA_API_KEY", "").strip()
        if single:
            keys = [single]
    return keys


def _mask(key: str) -> str:
    return f"...{key[-4:]}" if len(key) > 4 else "***"


def _raise_for_status(response: requests.Response) -> None:
    try:
        body = response.json()
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            message = body["error"].get("message") or str(body["error"])
        elif isinstance(body, dict):
            message = body.get("message") or response.text[:200]
        else:
            message = response.text[:200]
    except ValueError:
        message = response.text[:200]
    raise _HedraHTTPError(response.status_code, str(message))


def _classify_error(exc: Exception) -> str:
    """Returns 'client_error', 'rate_limit', 'auth', 'transient',
    'generation_failure', or 'unknown'."""
    if isinstance(exc, _HedraHTTPError):
        if exc.status_code in (400, 402, 404, 422):
            # 402 (INSUFFICIENT_BALANCE) added after direct reproduction
            # against the real v3 API (2026-08-20): the API wallet is
            # shared across every key on an account, so a balance failure
            # on one key fails identically on all of them — same
            # "raise immediately, don't burn through the pool" reasoning
            # as a malformed request.
            return "client_error"
        if exc.status_code == 429:
            return "rate_limit"
        if exc.status_code in (401, 403):
            return "auth"
        if exc.status_code >= 500:
            return "transient"
        return "unknown"
    if isinstance(exc, (HedraGenerationTimeoutError, HedraGenerationFailedError)):
        return "generation_failure"
    if isinstance(exc, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
        return "transient"
    return "unknown"


class HedraAvatarManager:
    def __init__(
        self,
        api_keys: list[str] | None = None,
        *,
        model: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        keys = api_keys if api_keys is not None else _load_keys_from_env()
        if not keys:
            raise ValueError(
                "No Hedra API keys configured — set HEDRA_API_KEYS "
                "(comma-separated) or HEDRA_API_KEY in the environment/.env"
            )
        self._keys: list[str] = keys
        self._cycle = itertools.cycle(self._keys)
        self._cooldown_until: dict[str, float] = {}
        self._session = session or requests.Session()
        # HEDRA_MODEL_ID/HEDRA_MODEL_SLUG (the old v2-era env var names) are
        # deliberately NOT read here — v3 has one model per URL path
        # (/models/{model}), not a body field selecting among several ids,
        # so a stale v2 model id in the environment would be silently
        # wrong rather than doing anything. HEDRA_MODEL (v3-shaped) is the
        # only override this client recognizes.
        self._model = model or os.environ.get("HEDRA_MODEL", DEFAULT_MODEL)

    def _next_available_key(self) -> str | None:
        now = time.monotonic()
        for _ in range(len(self._keys)):
            key = next(self._cycle)
            if self._cooldown_until.get(key, 0.0) <= now:
                return key
        return None

    # -- the public entry point ------------------------------------------------
    # Signature unchanged from the pre-v3 client — avatar_provider.py calls
    # this exact shape and needs no changes.

    def generate_avatar_video(
        self,
        image_path: str,
        audio_path: str,
        *,
        text_prompt: str = "",
        duration_ms: int | None = None,
        aspect_ratio: str = "9:16",
        resolution: str = "540p",
    ) -> bytes:
        """Runs the full Hedra v3 pipeline once per key in the pool (Tier
        1, horizontal rotation), stopping at the first success. Raises
        HedraRequestError immediately on a malformed-request response
        (retrying other keys won't fix a bad payload), or
        HedraAllKeysExhaustedError once every key has failed for any
        other reason (rate limit, auth, transient error, generation
        failure, or timeout)."""
        last_exc: Exception | None = None

        for _ in range(len(self._keys)):
            key = self._next_available_key()
            if key is None:
                key = next(self._cycle)  # every key cooling down — try anyway rather than stall

            try:
                return self._attempt(
                    key, image_path, audio_path, text_prompt=text_prompt,
                    duration_ms=duration_ms, aspect_ratio=aspect_ratio, resolution=resolution,
                )
            except _HedraHTTPError as exc:
                kind = _classify_error(exc)
                if kind == "client_error":
                    logger.error("generate_avatar_video: request rejected (%s) — not a key problem, raising immediately", exc)
                    raise HedraRequestError(exc.status_code, str(exc)) from exc
                last_exc = exc
                if kind in ("rate_limit", "auth"):
                    self._cooldown_until[key] = time.monotonic() + KEY_COOLDOWN_SECONDS
                logger.warning("generate_avatar_video: key %s failed (%s: %s) — trying next key", _mask(key), kind, exc)
            except (HedraGenerationTimeoutError, HedraGenerationFailedError) as exc:
                last_exc = exc
                logger.warning("generate_avatar_video: key %s's generation failed (%s) — trying next key", _mask(key), exc)
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                last_exc = exc
                logger.warning("generate_avatar_video: network error on key %s (%s) — trying next key", _mask(key), exc)

        logger.error("generate_avatar_video: all %d Hedra key(s) failed — last error: %s", len(self._keys), last_exc)
        raise HedraAllKeysExhaustedError(
            f"All {len(self._keys)} Hedra API key(s) failed: {last_exc}"
        ) from last_exc

    # -- one full attempt on one key --------------------------------------------

    def _attempt(
        self,
        key: str,
        image_path: str,
        audio_path: str,
        *,
        text_prompt: str,
        duration_ms: int | None,
        aspect_ratio: str,
        resolution: str,
    ) -> bytes:
        image_url = self._upload_file(key, image_path)
        audio_url = self._upload_file(key, audio_path)

        input_body: dict[str, Any] = {
            "prompt": text_prompt or "A person speaking naturally and warmly to the camera",
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
            "start_image": {"source": "url", "url": image_url},
            "audio": {"source": "url", "url": audio_url},
        }
        if duration_ms:
            input_body["duration_ms"] = duration_ms

        response = self._session.post(
            f"{HEDRA_BASE_URL}/models/{self._model}",
            headers={"X-API-Key": key},
            json={"input": input_body},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code >= 400:
            _raise_for_status(response)
        job_id = response.json()["job_id"]

        return self._poll_and_download(key, job_id)

    def _upload_file(self, key: str, file_path: str) -> str:
        """v3 has one generic upload endpoint for every media type — the
        returned presigned `url` is itself the file handle, passed back
        verbatim (query string included) as {"source": "url", "url": ...}
        in the next call's input."""
        headers = {"X-API-Key": key}
        name = Path(file_path).name

        with open(file_path, "rb") as fh:
            resp = self._session.post(
                f"{HEDRA_BASE_URL}/files",
                headers=headers,
                files={"file": (name, fh)},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        if resp.status_code >= 400:
            _raise_for_status(resp)
        url = resp.json().get("url")
        if not url:
            raise HedraGenerationFailedError(f"Hedra /files upload for {name!r} did not return a url")
        return url

    def _poll_and_download(self, key: str, job_id: str) -> bytes:
        headers = {"X-API-Key": key}
        deadline = time.monotonic() + POLL_TIMEOUT_SECONDS

        while time.monotonic() < deadline:
            resp = self._session.get(
                f"{HEDRA_BASE_URL}/jobs/{job_id}/status",
                headers=headers,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            if resp.status_code >= 400:
                _raise_for_status(resp)
            status = resp.json().get("status")

            if status == "COMPLETED":
                return self._download_result(key, job_id)

            if status == "FAILED":
                raise HedraGenerationFailedError(f"Job {job_id} ended with status=FAILED")

            time.sleep(POLL_INTERVAL_SECONDS)

        raise HedraGenerationTimeoutError(
            f"Job {job_id} did not reach a terminal status within {POLL_TIMEOUT_SECONDS:.0f}s"
        )

    def _download_result(self, key: str, job_id: str) -> bytes:
        headers = {"X-API-Key": key}
        resp = self._session.get(f"{HEDRA_BASE_URL}/jobs/{job_id}", headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
        if resp.status_code >= 400:
            _raise_for_status(resp)
        outputs = resp.json().get("outputs") or []
        if not outputs or not outputs[0].get("url"):
            raise HedraGenerationFailedError(f"Job {job_id} is COMPLETED but returned no output url")
        download_url = outputs[0]["url"]

        video_resp = requests.get(download_url, timeout=DOWNLOAD_TIMEOUT_SECONDS)
        video_resp.raise_for_status()
        return video_resp.content
