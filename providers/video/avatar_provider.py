"""AvatarFailoverProvider — the 3-tier resilient VideoGenerationProvider
for the 5-second talking-avatar hook:

  Tier 1 (horizontal):            HedraAvatarManager cycles HEDRA_API_KEYS.
  Tier 2 (vertical + horizontal): on HedraAllKeysExhaustedError/
      HedraRequestError, DIDAvatarManager cycles DID_API_KEYS.
  Tier 3 (local fallback):        on DIDAllKeysExhaustedError/
      DIDRequestError (or if neither pool is configured at all), returns
      the static fallback_assets/generic_hook.mp4 — auto-generated as a
      placeholder clip the first time it's needed, so a fresh checkout
      with zero API keys still produces a video instead of crashing.

Two documented, deliberate deviations from the strict Phase 0
VideoGenerationProvider interface (same pattern used throughout
providers/llm/gemini_provider.py and providers/tts/sarvam_tts_provider.py
— Python's ABC machinery only checks that a method name is overridden,
not its exact signature):
  1. `generate_scene` gains required keyword-only `image_path`,
     `audio_path`, and `project_id` arguments — Scene has no field for
     "here is the local image/audio to animate", and MediaAsset.project_id
     is required but not passed by the ABC signature.
  2. `generate_video` (storyboard -> final VideoAsset) is intentionally
     NOT implemented here. Per ADR-004 and rendering/interfaces/
     video_renderer.py's own docstring, multi-scene composition (avatar
     hook + B-roll + captions + transitions -> one VideoAsset) belongs to
     rendering.interfaces.VideoRenderer (Phase 4), not to a
     VideoGenerationProvider — this class only ever produces ONE
     talking-avatar clip per call. It raises a clear, explanatory
     NotImplementedError rather than faking a wrong implementation.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from core.interfaces.video_provider import VideoGenerationProvider
from core.models.enums import GenerationStatus, MediaAssetType
from core.models.media import AudioAsset, MediaAsset, VideoAsset
from core.models.storyboard import Scene, Storyboard
from providers.video.did_client import (
    DIDAllKeysExhaustedError,
    DIDAvatarManager,
    DIDRequestError,
)
from providers.video.hedra_client import (
    HedraAllKeysExhaustedError,
    HedraAvatarManager,
    HedraRequestError,
)

logger = logging.getLogger("vaanireach.providers.avatar_provider")

VIDEO_DIR = Path(os.environ.get("AVATAR_VIDEO_OUTPUT_DIR", "./data/video"))
FALLBACK_ASSET_PATH = Path(os.environ.get("AVATAR_FALLBACK_ASSET", "./fallback_assets/generic_hook.mp4"))
FALLBACK_DURATION_SECONDS = 5.0


def ensure_fallback_asset(
    path: str | Path = FALLBACK_ASSET_PATH, *, duration_seconds: float = FALLBACK_DURATION_SECONDS
) -> str:
    """Tier 3 of the avatar failover: a static local MP4 used only when
    both Hedra and D-ID are completely exhausted. Generates a minimal
    placeholder (solid color + silent audio track, via moviepy — backed
    by the imageio-ffmpeg binary it installs automatically, so no system
    ffmpeg is required) the first time it's needed, so a fresh checkout
    never crashes the demo just because nobody committed a real
    placeholder video."""
    fallback_path = Path(path)
    if fallback_path.exists() and fallback_path.stat().st_size > 0:
        return str(fallback_path)

    logger.warning("Tier 3 fallback asset missing — generating a placeholder at %s", fallback_path)
    fallback_path.parent.mkdir(parents=True, exist_ok=True)
    from moviepy import AudioClip, ColorClip  # local import: keep moviepy off the hot path when the real asset exists

    video = ColorClip(size=(720, 1280), color=(17, 24, 39), duration=duration_seconds).with_fps(24)
    silence = AudioClip(lambda t: 0.0, duration=duration_seconds, fps=24000)
    video = video.with_audio(silence)
    try:
        video.write_videofile(str(fallback_path), fps=24, codec="libx264", audio_codec="aac", logger=None)
    finally:
        video.close()
    return str(fallback_path)


class AvatarFailoverProvider(VideoGenerationProvider):
    def __init__(self, hedra: HedraAvatarManager | None = None, did: DIDAvatarManager | None = None) -> None:
        if hedra is not None:
            self.hedra: HedraAvatarManager | None = hedra
        else:
            try:
                self.hedra = HedraAvatarManager()
            except ValueError:
                logger.warning("No HEDRA_API_KEYS configured — Tier 1 (Hedra) will be skipped")
                self.hedra = None

        if did is not None:
            self.did: DIDAvatarManager | None = did
        else:
            try:
                self.did = DIDAvatarManager()
            except ValueError:
                logger.warning("No DID_API_KEYS configured — Tier 2 (D-ID) will be skipped")
                self.did = None

        self._job_status: dict[str, GenerationStatus] = {}

    # ---------------------------------------------------------------- the 3-tier cascade

    def generate_avatar_hook(
        self,
        image_path: str,
        audio_path: str,
        *,
        project_id: str,
        scene_id: str | None = None,
        text_prompt: str = "",
        aspect_ratio: str = "9:16",
    ) -> MediaAsset:
        """Runs the full 3-tier cascade and returns a MediaAsset pointing
        at a local MP4 — Hedra- or D-ID-generated on success, or the
        Tier 3 static placeholder if both vendor pools are exhausted.
        Never raises: a working video (even a placeholder one) is always
        returned so the Streamlit demo never crashes on this step.

        Worst-case wall-clock, undocumented elsewhere in the call chain
        (final whole-branch review, finding #5): each vendor tier retries
        every key in its pool, and each key's attempt can run all the way
        to that vendor's POLL_TIMEOUT_SECONDS (currently 300s) before
        being counted as failed and moving on — there is no shared/
        aggregate deadline across the cascade. So the theoretical worst
        case before falling through to Tier 3 is
        (num_hedra_keys x HEDRA POLL_TIMEOUT_SECONDS) +
        (num_did_keys x DID POLL_TIMEOUT_SECONDS)
        — e.g. 2 keys per vendor x 300s x 2 vendor tiers = 1200s (20
        minutes) per language. This is a known, accepted tradeoff for
        this fix wave (not something to redesign here) — a future reader
        adding an aggregate deadline should account for this shape.

        `aspect_ratio` (default "9:16", unchanged from before — so the
        separate dashboard pipeline, which never passes this, keeps its
        existing portrait-hook behavior) is forwarded to Hedra only.
        D-ID's client has no aspect_ratio parameter — its output shape
        follows the source `image_path`'s own dimensions, so getting a
        non-default shape out of D-ID means generating that shape of
        source portrait in the first place (see
        providers/video/avatar_portrait.py's AVATAR_IMAGE_WIDTH/HEIGHT)."""
        VIDEO_DIR.mkdir(parents=True, exist_ok=True)

        video_bytes: bytes | None = None
        provider_name: str | None = None
        tier: int | None = None

        if self.hedra is not None:
            try:
                video_bytes = self.hedra.generate_avatar_video(
                    image_path, audio_path, text_prompt=text_prompt, aspect_ratio=aspect_ratio,
                )
                provider_name, tier = "hedra", 1
            except (HedraAllKeysExhaustedError, HedraRequestError) as exc:
                logger.warning("generate_avatar_hook: Hedra tier failed (%s) — falling over to D-ID", exc)
            except Exception as exc:  # noqa: BLE001 - any hard failure also triggers the vertical failover
                logger.warning("generate_avatar_hook: unexpected Hedra failure (%s) — falling over to D-ID", exc)

        if video_bytes is None and self.did is not None:
            try:
                video_bytes = self.did.generate_avatar_video(image_path, audio_path)
                provider_name, tier = "d-id", 2
            except (DIDAllKeysExhaustedError, DIDRequestError) as exc:
                logger.warning("generate_avatar_hook: D-ID tier failed (%s) — falling back to the local static asset", exc)
            except Exception as exc:  # noqa: BLE001
                logger.warning("generate_avatar_hook: unexpected D-ID failure (%s) — falling back to the local static asset", exc)

        asset = MediaAsset(
            project_id=project_id,
            scene_id=scene_id,
            asset_type=MediaAssetType.VIDEO_CLIP,
            prompt_used=text_prompt or None,
            generation_status=GenerationStatus.COMPLETE,
        )

        if video_bytes is not None:
            out_path = VIDEO_DIR / f"{asset.id}.mp4"
            out_path.write_bytes(video_bytes)
            asset.storage_path = str(out_path)
            asset.provider_name = provider_name
            asset.metadata = {"tier": tier}
        else:
            logger.error("generate_avatar_hook: both Hedra and D-ID exhausted — using the Tier 3 static fallback asset")
            asset.storage_path = ensure_fallback_asset()
            asset.provider_name = "local-fallback"
            asset.metadata = {"tier": 3}

        self._job_status[asset.id] = GenerationStatus.COMPLETE
        return asset

    # ---------------------------------------------------------------- VideoGenerationProvider

    def generate_scene(
        self,
        scene: Scene,
        *,
        image_path: str,
        audio_path: str,
        project_id: str,
    ) -> MediaAsset:
        """See module docstring re: the added image_path/audio_path/
        project_id keyword-only arguments — a documented gap between this
        ABC's signature and what generating one avatar scene actually
        requires."""
        text_prompt = scene.visual_prompt or scene.narration_segment_text
        return self.generate_avatar_hook(
            image_path, audio_path, project_id=project_id, scene_id=scene.id, text_prompt=text_prompt
        )

    def generate_video(self, storyboard: Storyboard, audio_assets: list[AudioAsset]) -> VideoAsset:
        """See module docstring — multi-scene composition is
        rendering.interfaces.VideoRenderer's job (Phase 4), not this
        provider's. Deliberately not implemented rather than faking it."""
        raise NotImplementedError(
            "AvatarFailoverProvider.generate_video: multi-scene composition (avatar hook + "
            "B-roll + captions + transitions -> one VideoAsset) belongs to "
            "rendering.interfaces.VideoRenderer, not VideoGenerationProvider. Use "
            "generate_avatar_hook()/generate_scene() to produce one avatar clip, then pass it "
            "to a VideoRenderer alongside the B-roll scenes."
        )

    def get_status(self, job_id: str) -> GenerationStatus:
        """generate_avatar_hook() is fully synchronous — it only returns
        once a tier has succeeded or the fallback has been used — so
        there is no real async job to poll. An unrecognized id is
        reported FAILED rather than raising, since the ABC gives no "not
        found" signal."""
        status = self._job_status.get(job_id)
        if status is None:
            logger.warning("get_status: unknown job_id %s", job_id)
            return GenerationStatus.FAILED
        return status

    def cancel(self, job_id: str) -> None:
        """No-op: generate_avatar_hook() is fully synchronous/blocking,
        so there is no in-flight async job to cancel."""
        logger.info("cancel(%s): no-op — avatar generation is synchronous in this implementation", job_id)
