"""GroqManager — resilient multi-key wrapper around Groq's OpenAI-compatible
Chat Completions API.

Adopted in place of Gemini as the LLM backend for fact extraction, script
generation, translation, and semantic verification — Gemini's free-tier
daily quota (20 requests/day/key/model) proved far too tight for this
pipeline's real usage (repeatedly exhausted across all 3 configured keys
during live testing on 2026-08-20). Groq's free tier is dramatically more
generous (1000 requests / 8000 tokens per short window, confirmed live
against this project's key) and its LPU-based inference is consistently
sub-second — both a correctness and a speed win over Gemini.

Model choice, verified live against this project's Groq account on
2026-08-20 via `GET /openai/v1/models`: `openai/gpt-oss-120b` — the
largest general-purpose text model currently active for this account
(131072-token context), confirmed working for both plain-text and JSON
mode, and confirmed to produce fluent, accurate Hindi output (spot-checked
live, not assumed). `llama-3.3-70b-versatile` — an earlier, reasonable-
looking default — is not even in this account's model catalog anymore;
model lineups on Groq drift, so re-verify via that endpoint rather than
assuming a name is still valid before changing DEFAULT_TEXT_MODEL.

Same horizontal-key-rotation resilience shape as GeminiManager
(providers/llm/gemini_client.py): itertools.cycle + a per-key cooldown on
rate-limit/auth errors, short exponential backoff for transient
(5xx/network) errors before rotating to the next key. Never raises the
underlying HTTP exception to callers — only ever GroqAllKeysExhaustedError,
once every key in the pool has failed a full rotation.
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

import requests

logger = logging.getLogger("vaanireach.providers.groq")

T = TypeVar("T")

GROQ_CHAT_COMPLETIONS_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_TEXT_MODEL = "openai/gpt-oss-120b"

KEY_COOLDOWN_SECONDS = 60.0
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 8.0
MAX_RETRIES_PER_KEY = 2
REQUEST_TIMEOUT_SECONDS = 60.0

# Same heuristic-substring-marker approach as gemini_client.py's
# _classify_error, for the same reason: robust across the API's own
# point-release wording changes without pinning to exact error classes.
_RATE_LIMIT_MARKERS = ("429", "rate_limit", "rate limit", "too many requests")
_AUTH_QUOTA_MARKERS = ("401", "403", "invalid_api_key", "invalid api key", "insufficient_quota", "permission")
_TRANSIENT_MARKERS = ("500", "502", "503", "504", "internal", "unavailable", "timeout")


class GroqAllKeysExhaustedError(RuntimeError):
    """Raised when every key in the pool failed (rate-limit / quota / auth
    / repeated transient error) during one rotation. Callers must catch
    this and degrade gracefully — it is the designed failure mode, not a
    bug."""


class _GroqHTTPError(RuntimeError):
    """Internal — every non-200 response becomes one of these, then gets
    classified/handled inside call()'s retry loop."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"Groq API error {status_code}: {message}")
        self.status_code = status_code


def _load_keys_from_env(env_var: str = "GROQ_API_KEYS") -> list[str]:
    raw = os.environ.get(env_var, "")
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        single = os.environ.get("GROQ_API_KEY", "").strip()
        if single:
            keys = [single]
    return keys


def _classify_error(status_code: int | None, text: str) -> str:
    """Returns 'rate_limit', 'auth_quota', 'transient', or 'unknown'."""
    combined = f"{status_code} {text}".lower()
    if status_code == 429 or any(marker in combined for marker in _RATE_LIMIT_MARKERS):
        return "rate_limit"
    if status_code in (401, 403) or any(marker in combined for marker in _AUTH_QUOTA_MARKERS):
        return "auth_quota"
    if (isinstance(status_code, int) and status_code >= 500) or any(marker in combined for marker in _TRANSIENT_MARKERS):
        return "transient"
    return "unknown"


def _mask(key: str) -> str:
    return f"...{key[-4:]}" if len(key) > 4 else "***"


class GroqManager:
    """Cycles through a pool of Groq API keys. On a rate-limit/quota/auth
    error it puts that key on a cooldown and rotates to the next one
    immediately; on a transient error it retries the same key with short
    backoff before rotating. Raises GroqAllKeysExhaustedError only once a
    full rotation has found every key unusable. Talks to Groq's plain
    REST Chat Completions endpoint directly via `requests` rather than a
    vendor SDK client object — there is no official Groq Python SDK
    dependency in this project."""

    def __init__(self, api_keys: list[str] | None = None, *, session: requests.Session | None = None) -> None:
        keys = api_keys if api_keys is not None else _load_keys_from_env()
        if not keys:
            raise ValueError(
                "No Groq API keys configured — set GROQ_API_KEYS "
                "(comma-separated) or GROQ_API_KEY in the environment/.env"
            )
        self._keys: list[str] = keys
        self._cycle = itertools.cycle(self._keys)
        self._cooldown_until: dict[str, float] = {}
        self._session = session or requests.Session()

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

    def call(self, fn: Callable[[str], T], *, op_name: str = "groq_call") -> T:
        """Runs `fn(key)` with full horizontal-rotation + backoff
        resilience. `fn` should be a small closure performing exactly one
        Groq API request with the given key and returning its result."""
        last_exc: Exception | None = None
        attempts = 0
        max_attempts = len(self._keys) * (MAX_RETRIES_PER_KEY + 1)

        while attempts < max_attempts:
            key = self._next_available_key()
            if key is None:
                key = next(self._cycle)

            for retry in range(MAX_RETRIES_PER_KEY):
                attempts += 1
                try:
                    return fn(key)
                except _GroqHTTPError as exc:
                    last_exc = exc
                    kind = _classify_error(exc.status_code, str(exc))
                    if kind in ("rate_limit", "auth_quota"):
                        self._cooldown_until[key] = time.monotonic() + KEY_COOLDOWN_SECONDS
                        logger.warning(
                            "%s: key %s hit %s (%s) — rotating to next key",
                            op_name, _mask(key), kind, exc,
                        )
                        break
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
                except requests.exceptions.RequestException as exc:
                    last_exc = exc
                    if retry < MAX_RETRIES_PER_KEY - 1:
                        sleep_s = min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS * (2**retry)) + random.uniform(0, 0.5)
                        logger.warning(
                            "%s: network error on key %s (%s) — retrying in %.1fs",
                            op_name, _mask(key), exc, sleep_s,
                        )
                        time.sleep(sleep_s)
                        continue
                    logger.warning("%s: network error on key %s (%s) — rotating to next key", op_name, _mask(key), exc)
                    break

        logger.error(
            "%s: all %d Groq key(s) exhausted/failing — last error: %s",
            op_name, len(self._keys), last_exc,
        )
        raise GroqAllKeysExhaustedError(
            f"All {len(self._keys)} Groq API key(s) failed for {op_name}: {last_exc}"
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
        messages: list[dict[str, str]] = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
        if response_mime_type == "application/json":
            payload["response_format"] = {"type": "json_object"}

        def _do(key: str) -> str:
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            response = self._session.post(
                GROQ_CHAT_COMPLETIONS_URL, headers=headers, json=payload, timeout=REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code != 200:
                raise _GroqHTTPError(response.status_code, response.text[:300])
            body = response.json()
            choices = body.get("choices") or []
            text = choices[0]["message"]["content"] if choices else None
            if not text:
                raise RuntimeError("Groq returned an empty response (possibly content-filtered)")
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
        if the model returns something unparseable."""
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
        raise ValueError(f"Groq did not return parseable JSON after {max_parse_attempts} attempts: {last_error}")


def _parse_json_loose(text: str) -> Any:
    """Strips markdown code fences and parses the first balanced JSON
    value found in `text`. Identical logic to gemini_client.py's helper
    of the same name — kept as an independent copy rather than a shared
    import, matching this codebase's established pattern of each
    provider's client module being self-contained."""
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
