"""PollinationsVisualProvider — VisualProvider implementation backed by
Pollinations.ai's free, keyless REST image-generation API. Swapped in for
HuggingFaceVisualProvider (providers/visual/huggingface_provider.py, kept
in the codebase but no longer wired into the dashboard) once it turned
out Hugging Face's free `hf-inference` tier and the other backends its
Inference Providers router can route to (e.g. Together AI) also started
gating text-to-image behind a billing/deposit requirement — the same
problem that already forced the earlier move off Google Imagen 3 (see
ADR-004). Pollinations.ai needs neither an API key nor billing: a plain
unauthenticated GET against a public REST endpoint.

Resilience shape:
  Tier 0 (cache):     the exact same content-addressed LocalCache
                       (providers/visual/local_cache.py) every other
                       VisualProvider in this codebase uses — checked
                       before any network call.
  Tier 1 (retry):      a few quick retries with backoff on a transient
                       network failure or non-2xx/non-image response
                       (Pollinations is a free public queue and can
                       occasionally be slow or 5xx under load) before
                       giving up.
  Tier 2 (local fallback): if the API still fails after retries, falls
                       back to the exact same local placeholder card
                       every other VisualProvider uses
                       (providers/visual/placeholder.py) — written
                       outside the cache, so a later retry still
                       attempts a real generation instead of permanently
                       serving the placeholder for that prompt.

Documented deviation from the strict Phase 0 VisualProvider ABC (same
pattern as every other concrete provider in this codebase — Python's ABC
machinery only checks that a method name is overridden, not its exact
signature): `generate_image` gains a required keyword-only `project_id`
argument, because MediaAsset.project_id is required but neither the ABC
signature nor Scene carries one.
"""
from __future__ import annotations

import logging
import os
import time
import urllib.parse
from pathlib import Path

import requests

from core.interfaces.visual_provider import VisualProvider
from core.models.enums import GenerationStatus, MediaAssetType
from core.models.media import MediaAsset
from core.models.storyboard import Scene
from providers.visual.local_cache import LocalCache
from providers.visual.placeholder import write_placeholder_card

logger = logging.getLogger("vaanireach.providers.pollinations_visual_provider")

POLLINATIONS_BASE_URL = "https://image.pollinations.ai/prompt"
IMAGE_CACHE_DIR = Path(os.environ.get("IMAGE_CACHE_DIR", "./local_cache/images"))
IMAGE_WIDTH = int(os.environ.get("POLLINATIONS_IMAGE_WIDTH", "1080"))
IMAGE_HEIGHT = int(os.environ.get("POLLINATIONS_IMAGE_HEIGHT", "1920"))  # 1080x1920 = 9:16, matches the avatar hook clip

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2.0
REQUEST_TIMEOUT_SECONDS = 60.0


class PollinationsRequestError(RuntimeError):
    """Every non-recoverable request failure (timeout, network error, or
    a non-2xx/non-image response after all retries) becomes one of
    these — the provider catches it and falls back to the Tier 2 local
    placeholder rather than raising it further."""


class PollinationsVisualProvider(VisualProvider):
    def __init__(self, *, cache: LocalCache | None = None, session: requests.Session | None = None) -> None:
        self.cache = cache or LocalCache(IMAGE_CACHE_DIR, extension="jpg")
        self._session = session or requests.Session()
        self._job_status: dict[str, GenerationStatus] = {}

    # ---------------------------------------------------------------- VisualProvider

    def generate_image(self, prompt: str, scene: Scene, *, project_id: str) -> MediaAsset:
        """See module docstring re: the added `project_id` keyword-only
        argument. Checks the LocalCache first; on a miss, calls
        Pollinations.ai's free keyless endpoint with a few retries; if
        that still fails, falls back to a local placeholder card rather
        than raising, so a storyboard with 3 B-roll scenes never fails
        to produce 3 images just because the free image queue is briefly
        overloaded or timed out."""
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
            image_bytes = self._call_pollinations(prompt)
            stored_path = self.cache.put(prompt, image_bytes)
            asset.storage_path = stored_path
            asset.provider_name = "pollinations.ai"
            asset.metadata = {"cache_hit": False}
        except PollinationsRequestError as exc:
            logger.error(
                "generate_image: Pollinations.ai request failed for prompt=%r — using a local placeholder (%s)",
                prompt[:80], exc,
            )
            asset.storage_path = write_placeholder_card(self.cache, asset.id, prompt)
            asset.provider_name = "local-placeholder"
            asset.metadata = {"cache_hit": False, "fallback": "pollinations_request_failed"}

        asset.generation_status = GenerationStatus.COMPLETE
        self._job_status[asset.id] = GenerationStatus.COMPLETE
        return asset

    def get_status(self, job_id: str) -> GenerationStatus:
        """generate_image() is fully synchronous — it only returns once a
        cache hit, a real generation, or the placeholder fallback has
        produced a file — so there is no real async job to poll. An
        unrecognized id is reported FAILED rather than raising, since the
        ABC gives no "not found" signal (same convention as every other
        provider in this codebase)."""
        status = self._job_status.get(job_id)
        if status is None:
            logger.warning("get_status: unknown job_id %s", job_id)
            return GenerationStatus.FAILED
        return status

    def cancel(self, job_id: str) -> None:
        """No-op: generate_image() is fully synchronous/blocking, so
        there is no in-flight async job to cancel."""
        logger.info("cancel(%s): no-op — image generation is synchronous in this implementation", job_id)

    # ---------------------------------------------------------------- Pollinations.ai call

    def _call_pollinations(self, prompt: str) -> bytes:
        url = (
            f"{POLLINATIONS_BASE_URL}/{urllib.parse.quote(prompt)}"
            f"?width={IMAGE_WIDTH}&height={IMAGE_HEIGHT}&nologo=true"
        )

        last_error: Exception = PollinationsRequestError("no attempts were made")
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self._session.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
            except requests.exceptions.RequestException as exc:
                last_error = PollinationsRequestError(f"network error calling Pollinations.ai: {exc}")
                logger.warning("_call_pollinations: %s (attempt %d/%d)", last_error, attempt, MAX_RETRIES)
            else:
                content_type = response.headers.get("content-type", "")
                if response.status_code == 200 and content_type.startswith("image/"):
                    return response.content
                last_error = PollinationsRequestError(
                    f"Pollinations.ai returned {response.status_code} ({content_type}): {response.text[:200]}"
                )
                logger.warning("_call_pollinations: %s (attempt %d/%d)", last_error, attempt, MAX_RETRIES)

            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)

        raise last_error
