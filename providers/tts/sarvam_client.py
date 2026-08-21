"""SarvamTTSManager — resilient multi-key wrapper around Sarvam AI's
text-to-speech REST API.

Same resilience shape as GeminiManager (providers/llm/gemini_client.py):
horizontal key rotation via `itertools.cycle` with a per-key cooldown on
rate-limit/quota errors, short backoff+retry on transient 5xx/network
errors, and a `SarvamAllKeysExhaustedError` raised only once a full
rotation has found every key unusable.
`providers/tts/sarvam_tts_provider.py` is what falls further down to
edge-tts on that exception — this class's only job is exhausting the
Sarvam pool as gracefully and quickly as possible.

API contract, verified against docs.sarvam.ai/api-reference/text-to-speech
and docs.sarvam.ai/api/getting-started/errors-troubleshooting:
    POST https://api.sarvam.ai/text-to-speech
    header: api-subscription-key: <key>
    body:   {text, language_code, speaker, model, pace, temperature,
             speech_sample_rate, output_audio_codec}
    200:    {"request_id": str, "audios": [<base64 wav>, ...]}
    errors: 400 invalid params, 403 invalid key / forbidden, 413 payload
            too large, 422 validation failed, 429 rate limit OR quota
            exhausted (error.code distinguishes "rate_limit_exceeded_error"
            vs "insufficient_quota_error" — both handled the same way
            here: cool the key down and rotate), 500/503 transient.
"""
from __future__ import annotations

import base64
import io
import itertools
import logging
import os
import random
import re
import time
import wave
from typing import Callable, TypeVar

import requests

logger = logging.getLogger("vaanireach.providers.sarvam")

T = TypeVar("T")

SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"
DEFAULT_MODEL = "bulbul:v3"
DEFAULT_SPEAKER = "shubh"

# v3 accepts up to 2500 chars/request; kept with a safety margin.
MAX_CHARS_PER_REQUEST = 2000

REQUEST_TIMEOUT_SECONDS = 30
KEY_COOLDOWN_SECONDS = 60.0
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 8.0
MAX_RETRIES_PER_KEY = 2


class SarvamAllKeysExhaustedError(RuntimeError):
    """Raised when every key in the pool failed (rate-limit / quota / auth
    / repeated transient error) during one rotation. The TTSProvider layer
    catches this and falls back to edge-tts — this is the designed
    vertical-failover trigger, not a bug."""


class SarvamRequestError(RuntimeError):
    """A client-side request problem (400/413/422) — retrying with a
    different key would not help, since the request itself is invalid.
    Raised immediately without burning through the key pool."""

    def __init__(self, status_code: int, message: str, error_code: str | None = None) -> None:
        super().__init__(f"Sarvam API rejected the request ({status_code} {error_code}): {message}")
        self.status_code = status_code
        self.error_code = error_code


class _SarvamHTTPError(RuntimeError):
    """Internal — every non-2xx response becomes one of these inside the
    rotation loop, then gets classified/re-raised as the right public
    exception type."""

    def __init__(self, status_code: int, message: str, error_code: str | None = None) -> None:
        super().__init__(f"Sarvam API error {status_code} ({error_code}): {message}")
        self.status_code = status_code
        self.error_code = error_code


def _load_keys_from_env(env_var: str = "SARVAM_API_KEYS") -> list[str]:
    raw = os.environ.get(env_var, "")
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        single = os.environ.get("SARVAM_API_KEY", "").strip()
        if single:
            keys = [single]
    return keys


def _mask(key: str) -> str:
    return f"...{key[-4:]}" if len(key) > 4 else "***"


def _classify_error(exc: Exception) -> str:
    """Returns 'client_error', 'rate_limit', 'auth', 'transient', or 'unknown'."""
    if isinstance(exc, _SarvamHTTPError):
        if exc.status_code in (400, 413, 422):
            return "client_error"
        if exc.status_code == 429:
            return "rate_limit"
        if exc.status_code == 403:
            return "auth"
        if exc.status_code >= 500:
            return "transient"
        return "unknown"
    if isinstance(exc, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
        return "transient"
    return "unknown"


def _raise_for_status(response: requests.Response) -> None:
    try:
        body = response.json()
        err = body.get("error", {}) if isinstance(body, dict) else {}
        message = err.get("message") or response.text[:200]
        error_code = err.get("code")
    except ValueError:
        message = response.text[:200]
        error_code = None
    raise _SarvamHTTPError(response.status_code, message, error_code)


def _split_text(text: str, max_chars: int) -> list[str]:
    """Splits on sentence boundaries (., !, ?, and the Devanagari/Bengali
    danda ।) so each chunk stays under Sarvam's per-request character
    limit without cutting a sentence mid-word. Falls back to a hard slice
    for any single "sentence" that's still too long on its own."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    sentences = re.split(r"(?<=[.!?।])\s+", text)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) > max_chars and current:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)

    final: list[str] = []
    for chunk in chunks:
        if len(chunk) <= max_chars:
            final.append(chunk)
        else:
            final.extend(chunk[i : i + max_chars] for i in range(0, len(chunk), max_chars))
    return final


def _concat_wav_bytes(wav_chunks: list[bytes]) -> bytes:
    """Concatenates same-format WAV byte-strings via the stdlib `wave`
    module — no ffmpeg/moviepy needed for this, since every chunk came
    from the same Sarvam request shape (same sample rate/channels)."""
    if len(wav_chunks) == 1:
        return wav_chunks[0]

    readers = [wave.open(io.BytesIO(c), "rb") for c in wav_chunks]
    try:
        out_buffer = io.BytesIO()
        writer = wave.open(out_buffer, "wb")
        writer.setparams(readers[0].getparams())
        for reader in readers:
            writer.writeframes(reader.readframes(reader.getnframes()))
        writer.close()
        return out_buffer.getvalue()
    finally:
        for reader in readers:
            reader.close()


class SarvamTTSManager:
    """Cycles through a pool of Sarvam API keys. On 429 (rate limit or
    quota exhausted) or 403 (auth) it cools the key down and rotates
    immediately; on 5xx/network errors it retries the same key briefly
    before rotating; on 400/413/422 (our request is malformed) it raises
    straight away since no amount of key rotation fixes a bad request."""

    def __init__(self, api_keys: list[str] | None = None, *, session: requests.Session | None = None) -> None:
        keys = api_keys if api_keys is not None else _load_keys_from_env()
        if not keys:
            raise ValueError(
                "No Sarvam API keys configured — set SARVAM_API_KEYS "
                "(comma-separated) or SARVAM_API_KEY in the environment/.env"
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

    def call(self, fn: Callable[[str], T], *, op_name: str = "sarvam_call") -> T:
        last_exc: Exception | None = None
        attempts = 0
        max_attempts = len(self._keys) * (MAX_RETRIES_PER_KEY + 1)

        while attempts < max_attempts:
            key = self._next_available_key()
            if key is None:
                key = next(self._cycle)  # every key cooling down — try anyway rather than stall

            for retry in range(MAX_RETRIES_PER_KEY):
                attempts += 1
                try:
                    return fn(key)
                except _SarvamHTTPError as exc:
                    last_exc = exc
                    kind = _classify_error(exc)
                    if kind == "client_error":
                        logger.error("%s: request rejected (%s) — not a key problem, raising immediately", op_name, exc)
                        raise SarvamRequestError(exc.status_code, str(exc), exc.error_code) from exc
                    if kind in ("rate_limit", "auth"):
                        self._cooldown_until[key] = time.monotonic() + KEY_COOLDOWN_SECONDS
                        logger.warning("%s: key %s hit %s (%s) — rotating to next key", op_name, _mask(key), kind, exc)
                        break
                    if kind == "transient" and retry < MAX_RETRIES_PER_KEY - 1:
                        sleep_s = min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS * (2**retry)) + random.uniform(0, 0.5)
                        logger.warning("%s: transient error on key %s (%s) — retrying in %.1fs", op_name, _mask(key), exc, sleep_s)
                        time.sleep(sleep_s)
                        continue
                    logger.warning("%s: key %s failed (%s: %s) — rotating to next key", op_name, _mask(key), kind, exc)
                    break
                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                    last_exc = exc
                    if retry < MAX_RETRIES_PER_KEY - 1:
                        sleep_s = min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS * (2**retry)) + random.uniform(0, 0.5)
                        logger.warning("%s: network error on key %s (%s) — retrying in %.1fs", op_name, _mask(key), exc, sleep_s)
                        time.sleep(sleep_s)
                        continue
                    logger.warning("%s: network error on key %s (%s) — rotating to next key", op_name, _mask(key), exc)
                    break

        logger.error("%s: all %d Sarvam key(s) exhausted/failing — last error: %s", op_name, len(self._keys), last_exc)
        raise SarvamAllKeysExhaustedError(
            f"All {len(self._keys)} Sarvam API key(s) failed for {op_name}: {last_exc}"
        ) from last_exc

    # -- convenience wrappers -------------------------------------------------

    def synthesize_chunk(
        self,
        text: str,
        *,
        language_code: str,
        speaker: str = DEFAULT_SPEAKER,
        model: str = DEFAULT_MODEL,
        pace: float = 1.0,
        pitch: float | None = None,
        speech_sample_rate: int = 24000,
    ) -> bytes:
        """Returns raw WAV bytes for one chunk of text (<= MAX_CHARS_PER_REQUEST).

        pitch is only meaningful on model="bulbul:v2" — Sarvam silently
        ignores it on bulbul:v3 (see providers/tts/sarvam_voices.py's
        supports_pitch()). Passed through as-is here; the caller decides
        whether to offer it."""
        payload = {
            "text": text,
            "language_code": language_code,
            "speaker": speaker.lower(),
            "model": model,
            "pace": pace,
            "speech_sample_rate": speech_sample_rate,
            "output_audio_codec": "wav",
        }
        if pitch is not None:
            payload["pitch"] = pitch

        def _do(api_key: str) -> bytes:
            response = self._session.post(
                SARVAM_TTS_URL,
                headers={"api-subscription-key": api_key, "Content-Type": "application/json"},
                json=payload,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code >= 400:
                _raise_for_status(response)
            data = response.json()
            audios = data.get("audios") or []
            if not audios:
                raise RuntimeError("Sarvam TTS returned no audio")
            return base64.b64decode(audios[0])

        return self.call(_do, op_name=f"synthesize_chunk[{language_code}]")

    def synthesize_text(
        self,
        text: str,
        *,
        language_code: str,
        speaker: str = DEFAULT_SPEAKER,
        model: str = DEFAULT_MODEL,
        pace: float = 1.0,
        pitch: float | None = None,
    ) -> bytes:
        """Chunks long text across Sarvam's per-request character limit
        and stitches the resulting WAV chunks back into one file. If a
        later chunk fails and exhausts the whole key pool, the exception
        propagates rather than returning a half-synthesized track — the
        provider layer's job is deciding whether to fall back to edge-tts
        for the *entire* script, not to mix vendors mid-clip."""
        chunks = _split_text(text, MAX_CHARS_PER_REQUEST)
        if not chunks:
            raise ValueError("synthesize_text: empty text")
        wav_chunks = [
            self.synthesize_chunk(
                chunk, language_code=language_code, speaker=speaker, model=model, pace=pace, pitch=pitch,
            )
            for chunk in chunks
        ]
        return _concat_wav_bytes(wav_chunks)
