"""CloudflareFluxVisualProvider — VisualProvider implementation backed by
Cloudflare Workers AI's `@cf/black-forest-labs/flux-1-schnell` text-to-image
model. Adopted as the project's permanent image-generation provider —
flux-1-schnell is a distilled, few-step model built for low latency, and
Cloudflare's edge network keeps request latency low regardless of the
caller's location. Swapped in for PollinationsVisualProvider
(providers/visual/pollinations_visual_provider.py, kept in the codebase
but no longer wired into the dashboard).

Resilience shape:
  Tier 0 (cache):     the exact same content-addressed LocalCache
                       (providers/visual/local_cache.py) every other
                       VisualProvider in this codebase uses — checked
                       before any network call.
  Tier 1 (horizontal): CloudflareFluxManager (cloudflare_flux_client.py)
                       cycles CLOUDFLARE_API_TOKENS on a rate-limit/auth
                       error, the same itertools.cycle + cooldown shape
                       as GeminiManager/HedraAvatarManager/SarvamTTSManager.
  Tier 2 (local fallback): if Cloudflare isn't configured at all, or
                       every token is exhausted, falls back to the exact
                       same local placeholder card every other
                       VisualProvider uses (providers/visual/placeholder.py)
                       — written outside the cache, so a later retry
                       still attempts a real generation instead of
                       permanently serving the placeholder for that
                       prompt.

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
from pathlib import Path

from core.interfaces.visual_provider import VisualProvider
from core.models.enums import GenerationStatus, MediaAssetType
from core.models.media import MediaAsset
from core.models.storyboard import Scene
from providers.visual.cloudflare_flux_client import CloudflareAllTokensExhaustedError, CloudflareFluxManager
from providers.visual.local_cache import LocalCache
from providers.visual.placeholder import write_placeholder_card

logger = logging.getLogger("vaanireach.providers.cloudflare_flux_provider")

IMAGE_CACHE_DIR = Path(os.environ.get("IMAGE_CACHE_DIR", "./local_cache/images"))


class CloudflareFluxVisualProvider(VisualProvider):
    def __init__(self, manager: CloudflareFluxManager | None = None, *, cache: LocalCache | None = None) -> None:
        if manager is not None:
            self.manager: CloudflareFluxManager | None = manager
        else:
            try:
                self.manager = CloudflareFluxManager()
            except ValueError as exc:
                logger.warning(
                    "No Cloudflare account/tokens configured (%s) — every generate_image() call "
                    "this run will fall straight through to the local placeholder card", exc,
                )
                self.manager = None
        self.cache = cache or LocalCache(IMAGE_CACHE_DIR, extension="jpg")
        self._job_status: dict[str, GenerationStatus] = {}

    # ---------------------------------------------------------------- VisualProvider

    def generate_image(self, prompt: str, scene: Scene, *, project_id: str) -> MediaAsset:
        """See module docstring re: the added `project_id` keyword-only
        argument. Checks the LocalCache first; on a miss, calls
        flux-1-schnell through CloudflareFluxManager's token-rotation
        loop; if Cloudflare is unconfigured or every token is exhausted,
        falls back to a local placeholder card rather than raising, so a
        storyboard with several B-roll scenes never fails to produce
        images just because Cloudflare quota ran out."""
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

        image_bytes: bytes | None = None
        fallback_reason: str | None = None
        if self.manager is not None:
            try:
                image_bytes = self.manager.generate_image(prompt)
            except CloudflareAllTokensExhaustedError as exc:
                fallback_reason = f"cloudflare_tokens_exhausted: {exc}"
            except Exception as exc:  # noqa: BLE001 - any hard failure (incl. CloudflareRequestError) also falls back
                fallback_reason = f"cloudflare_request_failed: {exc}"
        else:
            fallback_reason = "cloudflare_not_configured"

        if image_bytes is not None:
            stored_path = self.cache.put(prompt, image_bytes)
            asset.storage_path = stored_path
            asset.provider_name = "cloudflare:flux-1-schnell"
            asset.metadata = {"cache_hit": False}
        else:
            logger.error(
                "generate_image: Cloudflare Flux unavailable for prompt=%r — using a local placeholder (%s)",
                prompt[:80], fallback_reason,
            )
            asset.storage_path = write_placeholder_card(self.cache, asset.id, prompt)
            asset.provider_name = "local-placeholder"
            asset.metadata = {"cache_hit": False, "fallback": fallback_reason}

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
