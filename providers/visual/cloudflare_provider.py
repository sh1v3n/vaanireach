"""CloudflareVisualProvider — VisualProvider implementation backed by
Cloudflare Workers AI's text-to-image models (FLUX.1-schnell by default).
Chosen over Hugging Face at the user's explicit request; same resilience
shape as every other provider in this codebase so it's a drop-in swap.

Auth needs BOTH a Cloudflare account id and an API token scoped to
Workers AI — unlike a single bearer key, the account id is part of the
URL path: POST /client/v4/accounts/{account_id}/ai/run/{model}.

Resilience shape (same as providers/visual/huggingface_provider.py):
  Tier 0 (cache):     the same content-addressed LocalCache every
                       VisualProvider in this codebase uses — checked
                       before any network call.
  Tier 1 (real call):  Cloudflare Workers AI, synchronous REST call.
  Tier 2 (local fallback): any failure (bad auth, model error, network)
                       falls back to the same local placeholder card
                       every other VisualProvider uses — written outside
                       the cache, so a later retry still attempts a real
                       generation instead of permanently serving the
                       placeholder for that prompt.

Documented deviation from the strict Phase 0 VisualProvider ABC (same
pattern as every other concrete provider in this codebase): `generate_image`
gains a required keyword-only `project_id` argument, because MediaAsset.
project_id is required but neither the ABC signature nor Scene carries one.
"""
from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

import requests

from core.interfaces.visual_provider import VisualProvider
from core.models.enums import GenerationStatus, MediaAssetType
from core.models.media import MediaAsset
from core.models.storyboard import Scene
from providers.visual.local_cache import LocalCache
from providers.visual.placeholder import write_placeholder_card

logger = logging.getLogger("vaanireach.providers.cloudflare_provider")

DEFAULT_MODEL = "@cf/black-forest-labs/flux-1-schnell"
IMAGE_CACHE_DIR = Path(os.environ.get("IMAGE_CACHE_DIR", "./local_cache/images"))
REQUEST_TIMEOUT_SECONDS = 60.0


class CloudflareRequestError(RuntimeError):
    """Every non-recoverable inference failure (bad auth, bad request,
    persistent error) becomes one of these — the provider catches it and
    falls back to the Tier 2 local placeholder rather than raising it
    further."""


class CloudflareVisualProvider(VisualProvider):
    def __init__(
        self,
        account_id: str | None = None,
        api_token: str | None = None,
        *,
        model: str | None = None,
        cache: LocalCache | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.account_id = (account_id if account_id is not None else os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")).strip()
        self.api_token = (api_token if api_token is not None else os.environ.get("CLOUDFLARE_API_TOKEN", "")).strip()
        if not self.account_id or not self.api_token:
            # Deliberately not raised (same reasoning as HuggingFaceVisualProvider):
            # a missing credential degrades to the Tier 2 placeholder rather than
            # blocking provider construction entirely.
            logger.warning(
                "CLOUDFLARE_ACCOUNT_ID/CLOUDFLARE_API_TOKEN not fully configured — "
                "every generate_image() call this run will fall straight through to the local placeholder card"
            )
        self.model = model or os.environ.get("CLOUDFLARE_IMAGE_MODEL", DEFAULT_MODEL)
        self.cache = cache or LocalCache(IMAGE_CACHE_DIR, extension="jpg")
        self._session = session or requests.Session()
        self._job_status: dict[str, GenerationStatus] = {}

    # ---------------------------------------------------------------- VisualProvider

    def generate_image(
        self, prompt: str, scene: Scene, *, project_id: str, width: int | None = None, height: int | None = None,
    ) -> MediaAsset:
        """See module docstring re: the added `project_id` keyword-only
        argument. Checks the LocalCache first; on a miss, calls Cloudflare
        Workers AI; if that fails, falls back to a local placeholder card
        rather than raising, so a storyboard's B-roll never fails to
        produce images just because the API call failed.

        `width`/`height` (optional, default None = whatever the model's
        own default is — 1024x1024 for FLUX.1-schnell, unspecified) let a
        caller request a specific output aspect ratio, e.g.
        providers/video/avatar_portrait.py requesting a 4:3 portrait so
        the avatar clip it drives fits a landscape PiP box without
        overflowing. The LocalCache key is still keyed on prompt text
        alone (unchanged) — every current caller uses one fixed
        width/height per distinct prompt, so this doesn't collide; a
        caller requesting the same prompt at two different sizes would
        need the cache key extended too, which isn't needed today."""
        prompt = (prompt or "").strip()
        if not prompt:
            raise ValueError("generate_image: prompt is empty")

        asset = MediaAsset(
            project_id=project_id,
            scene_id=scene.id,
            asset_type=MediaAssetType.IMAGE,
            prompt_used=prompt,
            generation_status=GenerationStatus.IN_PROGRESS,
        )

        cached_path = self.cache.get(prompt)
        if cached_path is not None:
            asset.storage_path = cached_path
            asset.provider_name = "local-cache"
            asset.generation_status = GenerationStatus.COMPLETE
            asset.metadata = {"cache_hit": True}
            self._job_status[asset.id] = GenerationStatus.COMPLETE
            return asset

        try:
            image_bytes = self._call_workers_ai(prompt, width=width, height=height)
            stored_path = self.cache.put(prompt, image_bytes)
            asset.storage_path = stored_path
            asset.provider_name = f"cloudflare:{self.model}"
            asset.metadata = {"cache_hit": False}
        except CloudflareRequestError as exc:
            logger.error(
                "generate_image: Cloudflare Workers AI failed for prompt=%r — using a local placeholder (%s)",
                prompt[:80], exc,
            )
            asset.storage_path = write_placeholder_card(self.cache, asset.id, prompt)
            asset.provider_name = "local-placeholder"
            asset.metadata = {"cache_hit": False, "fallback": "cloudflare_request_failed"}

        asset.generation_status = GenerationStatus.COMPLETE
        self._job_status[asset.id] = GenerationStatus.COMPLETE
        return asset

    def get_status(self, job_id: str) -> GenerationStatus:
        """generate_image() is fully synchronous — it only returns once a
        cache hit, a real generation, or the placeholder fallback has
        produced a file — so there is no real async job to poll. An
        unrecognized id is reported FAILED rather than raising, same
        convention as every other provider in this codebase."""
        status = self._job_status.get(job_id)
        if status is None:
            logger.warning("get_status: unknown job_id %s", job_id)
            return GenerationStatus.FAILED
        return status

    def cancel(self, job_id: str) -> None:
        """No-op: generate_image() is fully synchronous/blocking, so
        there is no in-flight async job to cancel."""
        logger.info("cancel(%s): no-op — image generation is synchronous in this implementation", job_id)

    # ---------------------------------------------------------------- Workers AI call

    def _call_workers_ai(self, prompt: str, *, width: int | None = None, height: int | None = None) -> bytes:
        if not self.account_id or not self.api_token:
            raise CloudflareRequestError("CLOUDFLARE_ACCOUNT_ID/CLOUDFLARE_API_TOKEN not configured")

        url = f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/ai/run/{self.model}"
        headers = {"Authorization": f"Bearer {self.api_token}"}

        payload: dict[str, object] = {"prompt": prompt}
        if width is not None:
            payload["width"] = width
        if height is not None:
            payload["height"] = height

        try:
            response = self._session.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
        except requests.exceptions.RequestException as exc:
            raise CloudflareRequestError(f"network error calling Cloudflare Workers AI: {exc}") from exc

        content_type = response.headers.get("content-type", "")

        if response.status_code == 200 and content_type.startswith("image/"):
            return response.content

        if response.status_code == 200 and content_type.startswith("application/json"):
            body = _safe_json(response)
            b64_image = (body.get("result") or {}).get("image") if isinstance(body, dict) else None
            if b64_image:
                try:
                    return base64.b64decode(b64_image)
                except Exception as exc:  # noqa: BLE001 - malformed base64 is a request error, not a crash
                    raise CloudflareRequestError(f"could not decode base64 image in response: {exc}") from exc
            raise CloudflareRequestError(f"200 OK but no image in response body: {response.text[:200]}")

        raise CloudflareRequestError(f"Workers AI returned {response.status_code}: {response.text[:300]}")


def _safe_json(response: requests.Response) -> dict:
    try:
        body = response.json()
        return body if isinstance(body, dict) else {}
    except ValueError:
        return {}
