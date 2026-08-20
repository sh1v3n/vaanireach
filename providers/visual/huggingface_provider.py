"""HuggingFaceVisualProvider — VisualProvider implementation backed by
Hugging Face's free Serverless Inference API. Swapped in for
GeminiImagenProvider (providers/visual/gemini_imagen_provider.py, kept
in the codebase but no longer wired into the dashboard) once it turned
out Google requires a billing-enabled Cloud project for Imagen access
even on ostensibly free-tier Gemini API keys — confirmed live against
every key configured for this project (every `generate_images` call
404'd with "not supported for predict"). Hugging Face's Inference API
needs only a free huggingface.co account + access token.

Endpoint/model note (verified live 2026-08-20): HF retired the old
`api-inference.huggingface.co` host — it no longer resolves at all.
Requests now go through the unified `router.huggingface.co` "Inference
Providers" gateway, which routes per-model to whichever backend actually
serves it; only a small, changing subset of models are available on the
*free* `hf-inference` provider specifically (both
`stabilityai/stable-diffusion-xl-base-1.0` and
`black-forest-labs/FLUX.1-schnell` returned 410 "deprecated ... no longer
supported by provider hf-inference" — despite being the two models
suggested when this integration was speced). Confirmed working:
`stabilityai/stable-diffusion-3-medium-diffusers`. If this one is
retired in turn, query
`https://huggingface.co/api/models?pipeline_tag=text-to-image&inference_provider=hf-inference`
for whatever's currently free, and set HF_IMAGE_MODEL.

Resilience shape:
  Tier 0 (cache):     the exact same content-addressed LocalCache
                       (providers/visual/local_cache.py) GeminiImagenProvider
                       uses — checked before any network call.
  Tier 1 (cold-start retry): HF's free serverless endpoints spin models
                       down when idle. A 503 whose body says the model is
                       "currently loading" (with an `estimated_time` in
                       seconds) means "come back shortly", not "failed" —
                       retried up to MAX_COLD_START_RETRIES times,
                       sleeping the reported `estimated_time` (or
                       DEFAULT_COLD_START_WAIT_SECONDS if none is given)
                       between attempts.
  Tier 2 (local fallback): any other failure (a non-cold-start error, or
                       cold-start retries exhausted) falls back to the
                       exact same local placeholder card
                       GeminiImagenProvider uses
                       (providers/visual/placeholder.py) — written
                       outside the cache, so a later retry still attempts
                       a real generation instead of permanently serving
                       the placeholder for that prompt.

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
from pathlib import Path

import requests

from core.interfaces.visual_provider import VisualProvider
from core.models.enums import GenerationStatus, MediaAssetType
from core.models.media import MediaAsset
from core.models.storyboard import Scene
from providers.visual.local_cache import LocalCache
from providers.visual.placeholder import write_placeholder_card

logger = logging.getLogger("vaanireach.providers.huggingface_provider")

HF_INFERENCE_BASE_URL = "https://router.huggingface.co/hf-inference/models"
DEFAULT_HF_MODEL = "stabilityai/stable-diffusion-3-medium-diffusers"
IMAGE_CACHE_DIR = Path(os.environ.get("IMAGE_CACHE_DIR", "./local_cache/images"))

MAX_COLD_START_RETRIES = 3
DEFAULT_COLD_START_WAIT_SECONDS = 15.0
REQUEST_TIMEOUT_SECONDS = 60.0


class HuggingFaceRequestError(RuntimeError):
    """Every non-recoverable inference failure (bad request, auth,
    persistent error, or cold-start retries exhausted) becomes one of
    these — the provider catches it and falls back to the Tier 2 local
    placeholder rather than raising it further."""


class HuggingFaceVisualProvider(VisualProvider):
    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str | None = None,
        cache: LocalCache | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = (api_key if api_key is not None else os.environ.get("HF_API_KEY", "")).strip()
        if not self.api_key:
            # Deliberately not raised (unlike GeminiManager, which has no
            # fallback and so must fail loudly): this provider always has
            # somewhere to land — an unauthenticated call 401s, which
            # generate_image()'s except HuggingFaceRequestError already
            # routes straight to the Tier 2 local placeholder. So a
            # missing key degrades gracefully instead of blocking
            # get_providers() from constructing this provider at all.
            logger.warning(
                "No HF_API_KEY configured — every generate_image() call this run will fall "
                "straight through to the local placeholder card"
            )
        self.model = model or os.environ.get("HF_IMAGE_MODEL", DEFAULT_HF_MODEL)
        self.cache = cache or LocalCache(IMAGE_CACHE_DIR, extension="jpg")
        self._session = session or requests.Session()
        self._job_status: dict[str, GenerationStatus] = {}

    # ---------------------------------------------------------------- VisualProvider

    def generate_image(self, prompt: str, scene: Scene, *, project_id: str) -> MediaAsset:
        """See module docstring re: the added `project_id` keyword-only
        argument. Checks the LocalCache first; on a miss, calls the HF
        Inference API with cold-start retry; if that still fails, falls
        back to a local placeholder card rather than raising, so a
        storyboard with 3 B-roll scenes never fails to produce 3 images
        just because the free inference endpoint is unavailable."""
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
            image_bytes = self._call_hf_inference(prompt)
            stored_path = self.cache.put(prompt, image_bytes)
            asset.storage_path = stored_path
            asset.provider_name = f"huggingface:{self.model}"
            asset.metadata = {"cache_hit": False}
        except HuggingFaceRequestError as exc:
            logger.error(
                "generate_image: Hugging Face inference failed for prompt=%r — using a local placeholder (%s)",
                prompt[:80], exc,
            )
            asset.storage_path = write_placeholder_card(self.cache, asset.id, prompt)
            asset.provider_name = "local-placeholder"
            asset.metadata = {"cache_hit": False, "fallback": "huggingface_request_failed"}

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

    # ---------------------------------------------------------------- HF Inference call

    def _call_hf_inference(self, prompt: str) -> bytes:
        url = f"{HF_INFERENCE_BASE_URL}/{self.model}"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = {"inputs": prompt}

        last_error: Exception = HuggingFaceRequestError("no attempts were made")
        for attempt in range(1, MAX_COLD_START_RETRIES + 2):  # +1 real attempt beyond the retries
            try:
                response = self._session.post(
                    url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT_SECONDS,
                )
            except requests.exceptions.RequestException as exc:
                last_error = HuggingFaceRequestError(f"network error calling HF inference: {exc}")
                logger.warning(
                    "_call_hf_inference: %s (attempt %d/%d)", last_error, attempt, MAX_COLD_START_RETRIES + 1,
                )
                continue

            content_type = response.headers.get("content-type", "")
            if response.status_code == 200 and content_type.startswith("image/"):
                return response.content

            if response.status_code == 503:
                body = _safe_json(response)
                error_text = str(body.get("error", "")).lower() if isinstance(body, dict) else ""
                if "loading" in error_text:
                    wait_seconds = DEFAULT_COLD_START_WAIT_SECONDS
                    if isinstance(body, dict) and body.get("estimated_time"):
                        try:
                            wait_seconds = float(body["estimated_time"])
                        except (TypeError, ValueError):
                            pass
                    if attempt <= MAX_COLD_START_RETRIES:
                        logger.warning(
                            "_call_hf_inference: model %s is cold-starting (estimated_time=%.1fs) — "
                            "waiting before retry %d/%d",
                            self.model, wait_seconds, attempt, MAX_COLD_START_RETRIES,
                        )
                        time.sleep(wait_seconds)
                        continue
                    last_error = HuggingFaceRequestError(
                        f"model {self.model} still cold-starting after {MAX_COLD_START_RETRIES} retries"
                    )
                    break

            # Any other non-2xx (or a 200 with a non-image body, e.g. a JSON error) —
            # not a cold-start, so retrying identically won't help.
            last_error = HuggingFaceRequestError(
                f"HF inference returned {response.status_code}: {response.text[:200]}"
            )
            break

        raise last_error


def _safe_json(response: requests.Response) -> dict:
    try:
        body = response.json()
        return body if isinstance(body, dict) else {}
    except ValueError:
        return {}
