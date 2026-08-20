"""CloudflareFluxManager — resilient multi-token wrapper around Cloudflare
Workers AI's `@cf/black-forest-labs/flux-1-schnell` text-to-image model.

Same key-rotation shape as GeminiManager/HedraAvatarManager/SarvamTTSManager
(itertools.cycle + a per-token cooldown on rate-limit/auth errors): one
Cloudflare account, potentially multiple API tokens (e.g. issued to
different team members, or scoped/rate-limited independently), cycled on
a 429/401/403 so a single exhausted or revoked token doesn't stall the
whole B-roll generation step.

API contract (Cloudflare Workers AI REST API), verified live against a
real account on 2026-08-20:
    POST https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/black-forest-labs/flux-1-schnell
    Headers: Authorization: Bearer <token>, Content-Type: application/json
    Body:    {"prompt": <prompt>, "steps": <int, 1-8, default 4>}
    Response: {"result": {"image": "<base64-encoded JPEG>", "usage": {...}},
               "success": bool, "errors": [...], "messages": [...]}
flux-1-schnell is a distilled, few-step model built specifically for low
latency — 4 steps (the default here) is Cloudflare's own recommended
value for interactive use.
"""
from __future__ import annotations

import base64
import itertools
import logging
import os
import time

import requests

logger = logging.getLogger("vaanireach.providers.cloudflare_flux")

CLOUDFLARE_BASE_URL = "https://api.cloudflare.com/client/v4/accounts"
FLUX_MODEL = "@cf/black-forest-labs/flux-1-schnell"

TOKEN_COOLDOWN_SECONDS = 60.0
REQUEST_TIMEOUT_SECONDS = 60.0
DEFAULT_STEPS = 4


class CloudflareAllTokensExhaustedError(RuntimeError):
    """Raised when every token in the pool failed (rate-limit / auth /
    repeated transient error) during one rotation. The visual provider
    catches this and falls back to the local placeholder card — this is
    the designed failure mode, not a bug."""


class CloudflareRequestError(RuntimeError):
    """A client-side request problem (400/404/422) — the same malformed
    request would fail identically on every token, so this is raised
    immediately without burning through the pool. The visual provider
    still treats it as "Cloudflare unavailable" and falls back to the
    placeholder card."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"Cloudflare API rejected the request ({status_code}): {message}")
        self.status_code = status_code


def _load_tokens_from_env(env_var: str = "CLOUDFLARE_API_TOKENS") -> list[str]:
    raw = os.environ.get(env_var, "")
    tokens = [t.strip() for t in raw.split(",") if t.strip()]
    if not tokens:
        single = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
        if single:
            tokens = [single]
    return tokens


def _mask(token: str) -> str:
    return f"...{token[-4:]}" if len(token) > 4 else "***"


class CloudflareFluxManager:
    """Cycles through a pool of Cloudflare API tokens for one account. On
    a rate-limit/auth error it puts that token on a cooldown and rotates
    to the next one immediately (no point burning retries on a token
    that's already dead for this window); on a transient (5xx/network)
    error it also rotates immediately, since flux-1-schnell calls are
    cheap/fast enough that trying a fresh token beats waiting out a
    backoff. Raises CloudflareAllTokensExhaustedError only once a full
    rotation has found every token unusable."""

    def __init__(
        self,
        account_id: str | None = None,
        api_tokens: list[str] | None = None,
        *,
        session: requests.Session | None = None,
    ) -> None:
        self.account_id = (account_id if account_id is not None else os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")).strip()
        if not self.account_id:
            raise ValueError(
                "No Cloudflare account configured — set CLOUDFLARE_ACCOUNT_ID in the environment/.env"
            )
        tokens = api_tokens if api_tokens is not None else _load_tokens_from_env()
        if not tokens:
            raise ValueError(
                "No Cloudflare API tokens configured — set CLOUDFLARE_API_TOKENS "
                "(comma-separated) or CLOUDFLARE_API_TOKEN in the environment/.env"
            )
        self._tokens: list[str] = tokens
        self._cycle = itertools.cycle(self._tokens)
        self._cooldown_until: dict[str, float] = {}
        self._session = session or requests.Session()

    def _next_available_token(self) -> str:
        """Advances the shared cycle up to one full lap looking for a
        token that isn't cooling down. Falls back to the next token in
        rotation anyway if every token currently is (relevant mainly for
        single-token pools) rather than stalling."""
        now = time.monotonic()
        for _ in range(len(self._tokens)):
            token = next(self._cycle)
            if self._cooldown_until.get(token, 0.0) <= now:
                return token
        return next(self._cycle)

    def generate_image(self, prompt: str, *, steps: int = DEFAULT_STEPS) -> bytes:
        """Runs the flux-1-schnell text-to-image call with full
        token-rotation resilience. Returns raw image bytes, decoded from
        the API's base64 response. Raises CloudflareRequestError
        immediately for a client-side problem (the same request would
        fail identically on every token) or
        CloudflareAllTokensExhaustedError once every token has failed a
        full rotation."""
        url = f"{CLOUDFLARE_BASE_URL}/{self.account_id}/ai/run/{FLUX_MODEL}"
        payload = {"prompt": prompt, "steps": steps}

        last_exc: Exception | None = None
        for _ in range(len(self._tokens)):
            token = self._next_available_token()
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

            try:
                response = self._session.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
            except requests.exceptions.RequestException as exc:
                last_exc = exc
                logger.warning("generate_image: network error on token %s (%s) — rotating to next token", _mask(token), exc)
                continue

            if response.status_code == 429:
                self._cooldown_until[token] = time.monotonic() + TOKEN_COOLDOWN_SECONDS
                last_exc = RuntimeError(f"rate limited (429): {response.text[:200]}")
                logger.warning("generate_image: token %s hit rate_limit — rotating to next token", _mask(token))
                continue

            if response.status_code in (401, 403):
                self._cooldown_until[token] = time.monotonic() + TOKEN_COOLDOWN_SECONDS
                last_exc = RuntimeError(f"auth error ({response.status_code}): {response.text[:200]}")
                logger.warning("generate_image: token %s failed auth (%s) — rotating to next token", _mask(token), response.status_code)
                continue

            if response.status_code in (400, 404, 422):
                raise CloudflareRequestError(response.status_code, response.text[:200])

            if response.status_code != 200:
                last_exc = RuntimeError(f"unexpected status {response.status_code}: {response.text[:200]}")
                logger.warning(
                    "generate_image: token %s got unexpected status %s — rotating to next token",
                    _mask(token), response.status_code,
                )
                continue

            try:
                body = response.json()
            except ValueError as exc:
                last_exc = RuntimeError(f"non-JSON response: {exc}")
                logger.warning("generate_image: token %s returned a non-JSON body — rotating to next token", _mask(token))
                continue

            result = body.get("result") if isinstance(body, dict) else None
            image_b64 = result.get("image") if isinstance(result, dict) else None
            if not body.get("success", True) or not image_b64:
                last_exc = RuntimeError(f"Cloudflare API returned no image: {body.get('errors') or body}")
                logger.warning(
                    "generate_image: token %s returned no image (%s) — rotating to next token",
                    _mask(token), body.get("errors"),
                )
                continue

            try:
                return base64.b64decode(image_b64)
            except (TypeError, ValueError) as exc:
                last_exc = RuntimeError(f"could not base64-decode response image: {exc}")
                logger.warning(
                    "generate_image: token %s returned undecodable image data — rotating to next token", _mask(token)
                )
                continue

        logger.error(
            "generate_image: all %d Cloudflare token(s) exhausted/failing — last error: %s",
            len(self._tokens), last_exc,
        )
        raise CloudflareAllTokensExhaustedError(
            f"All {len(self._tokens)} Cloudflare API token(s) failed: {last_exc}"
        ) from last_exc
