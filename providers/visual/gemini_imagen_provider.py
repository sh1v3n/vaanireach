"""GeminiImagenProvider — VisualProvider implementation backed by Google's
Imagen 3 model (imagen-3.0-generate-002), reused via the exact same
GeminiManager horizontal-key-rotation resilience loop Phase 1's
GeminiLLMProvider uses for text (see providers/llm/gemini_client.py) —
`GeminiManager.call()` is provider-call-agnostic by design specifically so
this class wouldn't need to reimplement rotation/backoff for images.

Resilience shape:
  Tier 0 (cache):     every prompt is looked up in a content-addressed
                       LocalCache (providers/visual/local_cache.py) before
                       any network call — see that module's docstring for
                       why this matters more here than anywhere else in
                       the pipeline.
  Tier 1 (horizontal): GeminiManager cycles GEMINI_API_KEYS on a cache miss.
  Tier 2 (local fallback): if every Gemini key is exhausted, a plain
                       solid-color placeholder card (drawn locally with
                       Pillow, no network) is generated instead — same
                       "never crash the demo" shape as
                       providers/video/avatar_provider.py's Tier 3 and
                       providers/tts/sarvam_tts_provider.py's edge-tts
                       fallback. Placeholders are written directly to the
                       returned asset's path, NOT through the cache, so a
                       later retry (once a key recovers) still attempts a
                       real generation instead of permanently serving the
                       placeholder for that prompt.

Documented deviation from the strict Phase 0 VisualProvider ABC (same
pattern as gemini_provider.py / avatar_provider.py / sarvam_tts_provider.py
— Python's ABC machinery only checks that a method name is overridden, not
its exact signature): `generate_image` gains a required keyword-only
`project_id` argument, because MediaAsset.project_id is required but
neither the ABC signature nor Scene carries one.
"""
from __future__ import annotations

import logging
import os
import textwrap
from pathlib import Path

from core.interfaces.visual_provider import VisualProvider
from core.models.enums import GenerationStatus, MediaAssetType
from core.models.media import MediaAsset
from core.models.storyboard import Scene
from providers.llm.gemini_client import GeminiAllKeysExhaustedError, GeminiManager
from providers.visual.local_cache import LocalCache

logger = logging.getLogger("vaanireach.providers.gemini_imagen_provider")

IMAGEN_MODEL = "imagen-3.0-generate-002"
IMAGE_CACHE_DIR = Path(os.environ.get("IMAGE_CACHE_DIR", "./local_cache/images"))
IMAGE_ASPECT_RATIO = os.environ.get("IMAGE_ASPECT_RATIO", "9:16")  # vertical, matches the avatar hook clip

_PLACEHOLDER_SIZE = (768, 1365)  # ~9:16, close enough for a fallback card
_PLACEHOLDER_BG = (30, 41, 59)  # slate-800 — neutral, readable with white text
_PLACEHOLDER_FG = (241, 245, 249)  # slate-100


class GeminiImagenProvider(VisualProvider):
    def __init__(self, manager: GeminiManager | None = None, cache: LocalCache | None = None) -> None:
        self.manager = manager or GeminiManager()
        self.cache = cache or LocalCache(IMAGE_CACHE_DIR, extension="jpg")
        self._job_status: dict[str, GenerationStatus] = {}

    # ---------------------------------------------------------------- VisualProvider

    def generate_image(self, prompt: str, scene: Scene, *, project_id: str) -> MediaAsset:
        """See module docstring re: the added `project_id` keyword-only
        argument. Checks the LocalCache first; on a miss, calls Imagen 3
        through GeminiManager's rotation/backoff loop; if every key is
        exhausted, falls back to a local placeholder card rather than
        raising, so a storyboard with 3 B-roll scenes never fails to
        produce 3 images just because image generation quota ran out."""
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
            image_bytes = self._call_imagen(prompt)
            stored_path = self.cache.put(prompt, image_bytes)
            asset.storage_path = stored_path
            asset.provider_name = f"gemini:{IMAGEN_MODEL}"
            asset.metadata = {"cache_hit": False}
        except GeminiAllKeysExhaustedError as exc:
            logger.error(
                "generate_image: all Gemini keys exhausted for prompt=%r — using a local placeholder (%s)",
                prompt[:80], exc,
            )
            asset.storage_path = self._write_placeholder(asset.id, prompt)
            asset.provider_name = "local-placeholder"
            asset.metadata = {"cache_hit": False, "fallback": "gemini_keys_exhausted"}

        asset.generation_status = GenerationStatus.COMPLETE
        self._job_status[asset.id] = GenerationStatus.COMPLETE
        return asset

    def get_status(self, job_id: str) -> GenerationStatus:
        """generate_image() is fully synchronous — it only returns once a
        cache hit, a real generation, or the placeholder fallback has
        produced a file — so there is no real async job to poll. An
        unrecognized id is reported FAILED rather than raising, since the
        ABC gives no "not found" signal (same convention as
        avatar_provider.py / sarvam_tts_provider.py)."""
        status = self._job_status.get(job_id)
        if status is None:
            logger.warning("get_status: unknown job_id %s", job_id)
            return GenerationStatus.FAILED
        return status

    def cancel(self, job_id: str) -> None:
        """No-op: generate_image() is fully synchronous/blocking, so
        there is no in-flight async job to cancel."""
        logger.info("cancel(%s): no-op — image generation is synchronous in this implementation", job_id)

    # ---------------------------------------------------------------- Imagen call

    def _call_imagen(self, prompt: str) -> bytes:
        from google.genai import types as genai_types  # local import: keep the SDK off the hot path on cache hits

        config = genai_types.GenerateImagesConfig(
            number_of_images=1,
            aspect_ratio=IMAGE_ASPECT_RATIO,
            output_mime_type="image/jpeg",
        )

        def _do(client):
            response = client.models.generate_images(model=IMAGEN_MODEL, prompt=prompt, config=config)
            generated = response.generated_images or []
            if not generated or generated[0].image is None or generated[0].image.image_bytes is None:
                reason = generated[0].rai_filtered_reason if generated else "no images returned"
                raise RuntimeError(f"Imagen returned no image data (possibly safety-filtered): {reason}")
            return generated[0].image.image_bytes

        return self.manager.call(_do, op_name=f"generate_image[{IMAGEN_MODEL}]")

    # ---------------------------------------------------------------- Tier 2: local placeholder

    def _write_placeholder(self, asset_id: str, prompt: str) -> str:
        """Draws a plain slate-colored card with the (wrapped) prompt text
        so the demo still has *something* in the right aspect ratio for
        every B-roll slot, and reviewers can immediately tell it's a
        stand-in rather than mistaking it for a real generation. Written
        under the cache root but with a `_placeholder_` prefix so it can
        never collide with (or be mistaken for) a real cached hit."""
        from PIL import Image, ImageDraw  # local import: keep Pillow off the hot path when Imagen succeeds

        out_path = self.cache.root / f"_placeholder_{asset_id}.jpg"
        img = Image.new("RGB", _PLACEHOLDER_SIZE, color=_PLACEHOLDER_BG)
        draw = ImageDraw.Draw(img)
        wrapped = textwrap.fill(prompt, width=28)
        text_bbox = draw.multiline_textbbox((0, 0), wrapped, spacing=10)
        text_w, text_h = text_bbox[2] - text_bbox[0], text_bbox[3] - text_bbox[1]
        x = max(0, (_PLACEHOLDER_SIZE[0] - text_w) // 2)
        y = max(0, (_PLACEHOLDER_SIZE[1] - text_h) // 2)
        draw.multiline_text((x, y), wrapped, fill=_PLACEHOLDER_FG, spacing=10, align="center")
        img.save(out_path, format="JPEG", quality=85)
        return str(out_path)
