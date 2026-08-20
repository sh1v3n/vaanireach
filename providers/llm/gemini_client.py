"""GeminiManager — resilient multi-key wrapper around the Gemini API.

Implements the two resilience patterns VaaniReach needs everywhere:
**horizontal key rotation** (this provider's key pool) via
`itertools.cycle`, with a per-key cooldown so an already
quota-exhausted key isn't retried again inside the same run, and a short
exponential backoff for transient (5xx/network) errors before rotating to
the next key.

This class is intentionally provider-call-agnostic: `call()` takes a
callable that receives a `genai.Client` and performs whatever request it
needs. `generate_text` / `generate_json` (used by Phase 1's fact/script/
translation/verification provider) are thin wrappers over it, and Phase
4's Imagen-backed VisualProvider reuses the exact same `call()` resilience
loop instead of re-implementing rotation for image generation.

Never raises the underlying SDK exception to callers — only ever
`GeminiAllKeysExhaustedError`, once every key in the pool has failed a
full rotation. Callers (the provider classes, then the orchestrator) are
expected to catch that and degrade gracefully so the Streamlit UI never
crashes during the demo.
"""
from __future__ import annotations

import itertools
import json
import logging
import os
import random
import re
import time
from typing import Any, Callable, TypeVar

from google import genai
from google.genai import types as genai_types

logger = logging.getLogger("vaanireach.providers.gemini")

T = TypeVar("T")

DEFAULT_TEXT_MODEL = "gemini-2.5-flash"

KEY_COOLDOWN_SECONDS = 60.0
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 8.0
MAX_RETRIES_PER_KEY = 2

# Heuristic error classification: matched against `str(exc).lower()` plus
# any HTTP-style status code the SDK exposes. Kept as substring markers
# (rather than importing exact google.genai.errors classes) so this stays
# correct across SDK point releases that may rename/restructure exception
# types — a real risk when we're pinning >= with no time to pin exact.
_RATE_LIMIT_MARKERS = ("429", "resource_exhausted", "rate limit", "quota")
_AUTH_QUOTA_MARKERS = ("403", "402", "permission_denied", "billing")
_TRANSIENT_MARKERS = ("500", "502", "503", "504", "internal", "unavailable", "deadline_exceeded", "timeout")


class GeminiAllKeysExhaustedError(RuntimeError):
    """Raised when every key in the pool failed (rate-limit / quota / auth
    / repeated transient error) during one rotation. Callers must catch
    this and degrade gracefully — it is the designed failure mode, not a
    bug."""


def _load_keys_from_env(env_var: str = "GEMINI_API_KEYS") -> list[str]:
    raw = os.environ.get(env_var, "")
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        single = os.environ.get("GEMINI_API_KEY", "").strip()
        if single:
            keys = [single]
    return keys


def _classify_error(exc: Exception) -> str:
    """Returns 'rate_limit', 'auth_quota', 'transient', or 'unknown'."""
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    text = f"{code} {exc}".lower()
    if code == 429 or any(marker in text for marker in _RATE_LIMIT_MARKERS):
        return "rate_limit"
    if code in (402, 403) or any(marker in text for marker in _AUTH_QUOTA_MARKERS):
        return "auth_quota"
    if (isinstance(code, int) and code >= 500) or any(marker in text for marker in _TRANSIENT_MARKERS):
        return "transient"
    return "unknown"


def _mask(key: str) -> str:
    return f"...{key[-4:]}" if len(key) > 4 else "***"


class GeminiManager:
    """Cycles through a pool of Gemini API keys. On a rate-limit/quota/auth
    error it puts that key on a cooldown and rotates to the next one
    immediately (no point burning retries on a key that's already dead
    for this window); on a transient error it retries the same key with
    short backoff before rotating. Raises GeminiAllKeysExhaustedError only
    once a full rotation has found every key unusable."""

    def __init__(self, api_keys: list[str] | None = None) -> None:
        keys = api_keys if api_keys is not None else _load_keys_from_env()
        if not keys:
            raise ValueError(
                "No Gemini API keys configured — set GEMINI_API_KEYS "
                "(comma-separated) or GEMINI_API_KEY in the environment/.env"
            )
        self._keys: list[str] = keys
        self._cycle = itertools.cycle(self._keys)
        self._clients: dict[str, genai.Client] = {}
        self._cooldown_until: dict[str, float] = {}

    def _client_for(self, key: str) -> genai.Client:
        client = self._clients.get(key)
        if client is None:
            client = genai.Client(api_key=key)
            self._clients[key] = client
        return client

    def _next_available_key(self) -> str | None:
        """Advances the shared cycle up to one full lap looking for a key
        that isn't cooling down. Returns None if every key currently is —
        the caller then falls back to trying the next one anyway rather
        than stalling (relevant mainly for single-key pools)."""
        now = time.monotonic()
        for _ in range(len(self._keys)):
            key = next(self._cycle)
            if self._cooldown_until.get(key, 0.0) <= now:
                return key
        return None

    def call(self, fn: Callable[[genai.Client], T], *, op_name: str = "gemini_call") -> T:
        """Runs `fn(client)` with full horizontal-rotation + backoff
        resilience. `fn` should be a small closure performing exactly one
        API request and returning its result — reused by both text
        generation here and Imagen image generation in Phase 4."""
        last_exc: Exception | None = None
        attempts = 0
        max_attempts = len(self._keys) * (MAX_RETRIES_PER_KEY + 1)

        while attempts < max_attempts:
            key = self._next_available_key()
            if key is None:
                # Every key is cooling down (only really bites with a
                # small/single-key pool) — try the next one in rotation
                # anyway rather than stalling the whole demo.
                key = next(self._cycle)

            client = self._client_for(key)
            for retry in range(MAX_RETRIES_PER_KEY):
                attempts += 1
                try:
                    return fn(client)
                except Exception as exc:  # noqa: BLE001 - classified immediately below
                    last_exc = exc
                    kind = _classify_error(exc)
                    if kind in ("rate_limit", "auth_quota"):
                        self._cooldown_until[key] = time.monotonic() + KEY_COOLDOWN_SECONDS
                        logger.warning(
                            "%s: key %s hit %s (%s) — rotating to next key",
                            op_name, _mask(key), kind, exc,
                        )
                        break  # rotate immediately, don't burn retries on a dead key
                    if kind == "transient" and retry < MAX_RETRIES_PER_KEY - 1:
                        sleep_s = min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS * (2**retry)) + random.uniform(0, 0.5)
                        logger.warning(
                            "%s: transient error on key %s (%s) — retrying in %.1fs",
                            op_name, _mask(key), exc, sleep_s,
                        )
                        time.sleep(sleep_s)
                        continue
                    logger.warning(
                        "%s: key %s failed (%s: %s) — rotating to next key",
                        op_name, _mask(key), kind, exc,
                    )
                    break

        logger.error(
            "%s: all %d Gemini key(s) exhausted/failing — last error: %s",
            op_name, len(self._keys), last_exc,
        )
        raise GeminiAllKeysExhaustedError(
            f"All {len(self._keys)} Gemini API key(s) failed for {op_name}: {last_exc}"
        ) from last_exc

    # -- convenience wrappers used by the provider layer ---------------------

    def generate_text(
        self,
        prompt: str,
        *,
        model: str = DEFAULT_TEXT_MODEL,
        system_instruction: str | None = None,
        temperature: float = 0.4,
        response_mime_type: str | None = None,
    ) -> str:
        config_kwargs: dict[str, Any] = {"temperature": temperature}
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction
        if response_mime_type:
            config_kwargs["response_mime_type"] = response_mime_type
        config = genai_types.GenerateContentConfig(**config_kwargs)

        def _do(client: genai.Client) -> str:
            response = client.models.generate_content(model=model, contents=prompt, config=config)
            text = getattr(response, "text", None)
            if not text:
                raise RuntimeError("Gemini returned an empty response (possibly safety-blocked)")
            return text

        return self.call(_do, op_name=f"generate_text[{model}]")

    def generate_json(
        self,
        prompt: str,
        *,
        model: str = DEFAULT_TEXT_MODEL,
        system_instruction: str | None = None,
        temperature: float = 0.2,
        max_parse_attempts: int = 2,
    ) -> Any:
        """Requests JSON-mode output and parses it, retrying the whole
        generation (not just the parse) up to `max_parse_attempts` times
        if the model returns something unparseable — occasionally happens
        even with response_mime_type='application/json' on truncated or
        borderline outputs."""
        last_error: Exception | None = None
        for attempt in range(max_parse_attempts):
            effective_prompt = prompt
            if attempt > 0:
                effective_prompt = (
                    f"{prompt}\n\nYour previous reply was not valid JSON. Return ONLY a single "
                    "valid JSON value — no markdown fences, no commentary."
                )
            text = self.generate_text(
                effective_prompt,
                model=model,
                system_instruction=system_instruction,
                temperature=temperature,
                response_mime_type="application/json",
            )
            try:
                return _parse_json_loose(text)
            except ValueError as exc:
                last_error = exc
                logger.warning("generate_json: parse attempt %d/%d failed: %s", attempt + 1, max_parse_attempts, exc)
        raise ValueError(f"Gemini did not return parseable JSON after {max_parse_attempts} attempts: {last_error}")


def _parse_json_loose(text: str) -> Any:
    """Strips markdown code fences and parses the first balanced JSON
    value found in `text`. Gemini is asked for pure JSON via
    response_mime_type, but this tolerates the occasional stray fence or
    leading/trailing prose anyway."""
    stripped = text.strip()
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.IGNORECASE | re.MULTILINE).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    start = next((i for i, ch in enumerate(stripped) if ch in "[{"), None)
    if start is None:
        raise ValueError(f"No JSON object/array found in response: {text[:200]!r}")

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(stripped)):
        ch = stripped[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 0:
                return json.loads(stripped[start : i + 1])
    raise ValueError(f"Unbalanced JSON in response: {text[:200]!r}")
