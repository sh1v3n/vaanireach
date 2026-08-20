"""HedraAvatarManager — resilient multi-key wrapper around Hedra's
Character-3 talking-avatar API.

Same key-rotation shape as GeminiManager/SarvamTTSManager (itertools.cycle
+ per-key cooldown on rate-limit/auth errors), but unlike a single text or
TTS call, one "attempt" here is a whole asynchronous pipeline — upload
image, upload audio, trigger generation, poll until done — so rotation
happens once per FULL attempt: a failed/timed-out generation on key 1
means "run the whole pipeline again on key 2", not "back off and retry
key 1".

API contract, verified against hedra.com/docs/api-reference/public/* and
the official hedra-labs/hedra-api-starter reference implementation
(github.com/hedra-labs/hedra-api-starter):
    Base URL: https://api.hedra.com/web-app/public
    Auth:     X-API-Key: <key>

    1. POST /assets                {name, type:"image"}  -> {id}
       POST /assets/{id}/upload    multipart file=<image bytes>
    2. POST /assets                {name, type:"audio"}  -> {id}
       POST /assets/{id}/upload    multipart file=<audio bytes>
    3. POST /generations           {type:"video", ai_model_id|model_slug,
                                     start_keyframe_id, audio_id,
                                     generated_video_inputs:{text_prompt,
                                     resolution, aspect_ratio,
                                     duration_ms?}}       -> {id, status}
    4. GET  /generations/{id}/status  (poll every ~5s)    -> {status,
                                     progress, download_url, error_message}
       status in {queued, processing, finalizing, complete, error}
    5. GET <download_url> (unauthenticated, presigned)    -> mp4 bytes

`ai_model_id` defaults to the Character-3 id used in Hedra's own starter
repo (`d1dd37a3-e39a-4854-a298-6510289f9cf2`); Hedra's public schema also
now accepts a `model_slug` string (documented as the non-deprecated
replacement) — set HEDRA_MODEL_SLUG to use that instead if Hedra rotates
model ids again before the demo.
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

HEDRA_BASE_URL = "https://api.hedra.com/web-app/public"
DEFAULT_MODEL_ID = "d1dd37a3-e39a-4854-a298-6510289f9cf2"

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
    """A generation job reached status=='error' (or returned no
    download_url on completion)."""


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
        message = (body.get("message") or body.get("error") or response.text[:200]) if isinstance(body, dict) else response.text[:200]
    except ValueError:
        message = response.text[:200]
    raise _HedraHTTPError(response.status_code, str(message))


def _classify_error(exc: Exception) -> str:
    """Returns 'client_error', 'rate_limit', 'auth', 'transient',
    'generation_failure', or 'unknown'."""
    if isinstance(exc, _HedraHTTPError):
        if exc.status_code in (400, 404, 422):
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
        model_id: str | None = None,
        model_slug: str | None = None,
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
        self._model_id = model_id or os.environ.get("HEDRA_MODEL_ID", DEFAULT_MODEL_ID)
        self._model_slug = model_slug or os.environ.get("HEDRA_MODEL_SLUG") or None

    def _next_available_key(self) -> str | None:
        now = time.monotonic()
        for _ in range(len(self._keys)):
            key = next(self._cycle)
            if self._cooldown_until.get(key, 0.0) <= now:
                return key
        return None

    # -- the public entry point ------------------------------------------------

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
        """Runs the full Hedra pipeline once per key in the pool (Tier 1,
        horizontal rotation), stopping at the first success. Raises
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
        image_asset_id = self._create_and_upload_asset(key, image_path, asset_type="image")
        audio_asset_id = self._create_and_upload_asset(key, audio_path, asset_type="audio")

        generated_video_inputs: dict[str, Any] = {
            "text_prompt": text_prompt or "A person speaking naturally and warmly to the camera",
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
        }
        if duration_ms:
            generated_video_inputs["duration_ms"] = duration_ms

        payload: dict[str, Any] = {
            "type": "video",
            "start_keyframe_id": image_asset_id,
            "audio_id": audio_asset_id,
            "generated_video_inputs": generated_video_inputs,
        }
        if self._model_slug:
            payload["model_slug"] = self._model_slug
        else:
            payload["ai_model_id"] = self._model_id

        response = self._session.post(
            f"{HEDRA_BASE_URL}/generations",
            headers={"X-API-Key": key},
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code >= 400:
            _raise_for_status(response)
        generation_id = response.json()["id"]

        return self._poll_and_download(key, generation_id)

    def _create_and_upload_asset(self, key: str, file_path: str, *, asset_type: str) -> str:
        headers = {"X-API-Key": key}
        name = Path(file_path).name

        create_resp = self._session.post(
            f"{HEDRA_BASE_URL}/assets",
            headers=headers,
            json={"name": name, "type": asset_type},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if create_resp.status_code >= 400:
            _raise_for_status(create_resp)
        asset_id = create_resp.json()["id"]

        with open(file_path, "rb") as fh:
            upload_resp = self._session.post(
                f"{HEDRA_BASE_URL}/assets/{asset_id}/upload",
                headers=headers,
                files={"file": (name, fh)},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        if upload_resp.status_code >= 400:
            _raise_for_status(upload_resp)
        return asset_id

    def _poll_and_download(self, key: str, generation_id: str) -> bytes:
        headers = {"X-API-Key": key}
        deadline = time.monotonic() + POLL_TIMEOUT_SECONDS

        while time.monotonic() < deadline:
            resp = self._session.get(
                f"{HEDRA_BASE_URL}/generations/{generation_id}/status",
                headers=headers,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            if resp.status_code >= 400:
                _raise_for_status(resp)
            data = resp.json()
            status = data.get("status")

            if status == "complete":
                download_url = data.get("download_url") or data.get("url")
                if not download_url:
                    raise HedraGenerationFailedError(f"Generation {generation_id} completed but returned no download_url")
                video_resp = requests.get(download_url, timeout=DOWNLOAD_TIMEOUT_SECONDS)
                video_resp.raise_for_status()
                return video_resp.content

            if status == "error":
                raise HedraGenerationFailedError(
                    f"Generation {generation_id} failed: {data.get('error_message') or data.get('error')}"
                )

            time.sleep(POLL_INTERVAL_SECONDS)

        raise HedraGenerationTimeoutError(
            f"Generation {generation_id} did not reach a terminal status within {POLL_TIMEOUT_SECONDS:.0f}s"
        )
