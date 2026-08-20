"""DIDAvatarManager — resilient multi-key wrapper around D-ID's Talks API.

Same shape as HedraAvatarManager: horizontal key rotation via
itertools.cycle, where one "attempt" is a full pipeline (upload image,
upload audio, create talk, poll until done) run once per key.

API contract, verified against docs.d-id.com:
    Base URL: https://api.d-id.com
    Auth:     Authorization: Basic base64(<key>) — D-ID issues API keys
              already in "username:password" form; the whole string is
              base64-encoded here, not just a token.

    1. POST /images  multipart field "image" -> {url}  (temp hosted image URL, 24-48h TTL)
    2. POST /audios  multipart field "audio" -> {url}  (temp hosted audio URL, 24-48h TTL)
    3. POST /talks   {source_url, script:{type:"audio", audio_url},
                       config:{result_format:"mp4"}}   -> {id, status:"created"}
    4. GET  /talks/{id}  (poll every ~5s)              -> {status, result_url}
       status in {created, started, done, error, rejected}
    5. GET <result_url> (unauthenticated, presigned s3) -> mp4 bytes
"""
from __future__ import annotations

import base64
import itertools
import logging
import os
import time
from pathlib import Path

import requests

logger = logging.getLogger("vaanireach.providers.did")

DID_BASE_URL = "https://api.d-id.com"

POLL_INTERVAL_SECONDS = 5.0
POLL_TIMEOUT_SECONDS = 120.0
REQUEST_TIMEOUT_SECONDS = 30.0
DOWNLOAD_TIMEOUT_SECONDS = 60.0
KEY_COOLDOWN_SECONDS = 60.0


class DIDAllKeysExhaustedError(RuntimeError):
    """Every key in the pool failed (rate-limit / auth / repeated
    transient error / generation failure / timeout) across one full
    rotation. The avatar provider catches this and falls back to the
    Tier 3 local static asset."""


class DIDRequestError(RuntimeError):
    """A client-side request problem (400/422) — the same malformed
    request would fail identically on every key, so this is raised
    immediately without burning through the pool."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"D-ID API rejected the request ({status_code}): {message}")
        self.status_code = status_code


class DIDGenerationTimeoutError(RuntimeError):
    """A talk did not reach a terminal status within POLL_TIMEOUT_SECONDS
    — treated as a silent failure per spec, chained as the cause when the
    key pool is exhausted."""


class DIDGenerationFailedError(RuntimeError):
    """A talk reached status=='error'/'rejected' (or returned no
    result_url on completion)."""


class _DIDHTTPError(RuntimeError):
    """Internal — every non-2xx response becomes one of these, then gets
    classified/re-raised as the right public exception type."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"D-ID API error {status_code}: {message}")
        self.status_code = status_code


def _load_keys_from_env(env_var: str = "DID_API_KEYS") -> list[str]:
    raw = os.environ.get(env_var, "")
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        single = os.environ.get("DID_API_KEY", "").strip()
        if single:
            keys = [single]
    return keys


def _mask(key: str) -> str:
    return f"...{key[-4:]}" if len(key) > 4 else "***"


def _auth_header(key: str) -> dict[str, str]:
    token = base64.b64encode(key.encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _raise_for_status(response: requests.Response) -> None:
    try:
        body = response.json()
        message = (body.get("description") or body.get("message") or response.text[:200]) if isinstance(body, dict) else response.text[:200]
    except ValueError:
        message = response.text[:200]
    raise _DIDHTTPError(response.status_code, str(message))


def _classify_error(exc: Exception) -> str:
    """Returns 'client_error', 'rate_limit', 'auth', 'transient',
    'generation_failure', or 'unknown'."""
    if isinstance(exc, _DIDHTTPError):
        if exc.status_code in (400, 422):
            return "client_error"
        if exc.status_code == 429:
            return "rate_limit"
        if exc.status_code in (401, 403):
            return "auth"
        if exc.status_code >= 500:
            return "transient"
        return "unknown"
    if isinstance(exc, (DIDGenerationTimeoutError, DIDGenerationFailedError)):
        return "generation_failure"
    if isinstance(exc, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
        return "transient"
    return "unknown"


class DIDAvatarManager:
    def __init__(self, api_keys: list[str] | None = None, *, session: requests.Session | None = None) -> None:
        keys = api_keys if api_keys is not None else _load_keys_from_env()
        if not keys:
            raise ValueError(
                "No D-ID API keys configured — set DID_API_KEYS "
                "(comma-separated) or DID_API_KEY in the environment/.env"
            )
        self._keys: list[str] = keys
        self._cycle = itertools.cycle(self._keys)
        self._cooldown_until: dict[str, float] = {}
        self._session = session or requests.Session()

    def _next_available_key(self) -> str | None:
        now = time.monotonic()
        for _ in range(len(self._keys)):
            key = next(self._cycle)
            if self._cooldown_until.get(key, 0.0) <= now:
                return key
        return None

    # -- the public entry point ------------------------------------------------

    def generate_avatar_video(self, image_path: str, audio_path: str) -> bytes:
        """Runs the full D-ID pipeline once per key in the pool (Tier 2's
        own horizontal rotation), stopping at the first success. Raises
        DIDRequestError immediately on a malformed-request response, or
        DIDAllKeysExhaustedError once every key has failed for any other
        reason."""
        last_exc: Exception | None = None

        for _ in range(len(self._keys)):
            key = self._next_available_key()
            if key is None:
                key = next(self._cycle)

            try:
                return self._attempt(key, image_path, audio_path)
            except _DIDHTTPError as exc:
                kind = _classify_error(exc)
                if kind == "client_error":
                    logger.error("generate_avatar_video: request rejected (%s) — not a key problem, raising immediately", exc)
                    raise DIDRequestError(exc.status_code, str(exc)) from exc
                last_exc = exc
                if kind in ("rate_limit", "auth"):
                    self._cooldown_until[key] = time.monotonic() + KEY_COOLDOWN_SECONDS
                logger.warning("generate_avatar_video: key %s failed (%s: %s) — trying next key", _mask(key), kind, exc)
            except (DIDGenerationTimeoutError, DIDGenerationFailedError) as exc:
                last_exc = exc
                logger.warning("generate_avatar_video: key %s's talk failed (%s) — trying next key", _mask(key), exc)
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                last_exc = exc
                logger.warning("generate_avatar_video: network error on key %s (%s) — trying next key", _mask(key), exc)

        logger.error("generate_avatar_video: all %d D-ID key(s) failed — last error: %s", len(self._keys), last_exc)
        raise DIDAllKeysExhaustedError(
            f"All {len(self._keys)} D-ID API key(s) failed: {last_exc}"
        ) from last_exc

    # -- one full attempt on one key --------------------------------------------

    def _attempt(self, key: str, image_path: str, audio_path: str) -> bytes:
        image_url = self._upload(key, image_path, endpoint="images", field="image")
        audio_url = self._upload(key, audio_path, endpoint="audios", field="audio")

        payload = {
            "source_url": image_url,
            "script": {"type": "audio", "audio_url": audio_url},
            "config": {"result_format": "mp4"},
        }
        response = self._session.post(
            f"{DID_BASE_URL}/talks",
            headers={**_auth_header(key), "Content-Type": "application/json"},
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code >= 400:
            _raise_for_status(response)
        talk_id = response.json()["id"]

        return self._poll_and_download(key, talk_id)

    def _upload(self, key: str, file_path: str, *, endpoint: str, field: str) -> str:
        headers = _auth_header(key)
        name = Path(file_path).name

        with open(file_path, "rb") as fh:
            resp = self._session.post(
                f"{DID_BASE_URL}/{endpoint}",
                headers=headers,
                files={field: (name, fh)},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        if resp.status_code >= 400:
            _raise_for_status(resp)
        url = resp.json().get("url")
        if not url:
            raise DIDGenerationFailedError(f"D-ID /{endpoint} upload did not return a url")
        return url

    def _poll_and_download(self, key: str, talk_id: str) -> bytes:
        headers = _auth_header(key)
        deadline = time.monotonic() + POLL_TIMEOUT_SECONDS

        while time.monotonic() < deadline:
            resp = self._session.get(f"{DID_BASE_URL}/talks/{talk_id}", headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
            if resp.status_code >= 400:
                _raise_for_status(resp)
            data = resp.json()
            status = data.get("status")

            if status == "done":
                result_url = data.get("result_url")
                if not result_url:
                    raise DIDGenerationFailedError(f"Talk {talk_id} is done but returned no result_url")
                video_resp = requests.get(result_url, timeout=DOWNLOAD_TIMEOUT_SECONDS)
                video_resp.raise_for_status()
                return video_resp.content

            if status in ("error", "rejected"):
                raise DIDGenerationFailedError(f"Talk {talk_id} ended with status={status!r}")

            time.sleep(POLL_INTERVAL_SECONDS)

        raise DIDGenerationTimeoutError(
            f"Talk {talk_id} did not reach a terminal status within {POLL_TIMEOUT_SECONDS:.0f}s"
        )
