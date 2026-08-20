"""AvatarFailoverProvider — the 3-tier resilient VideoGenerationProvider
for the 5-second talking-avatar hook:

  Tier 1 (horizontal):            HedraAvatarManager cycles HEDRA_API_KEYS.
  Tier 2 (vertical + horizontal): on HedraAllKeysExhaustedError/
      HedraRequestError, DIDAvatarManager cycles DID_API_KEYS.
  Tier 3 (local fallback):        on DIDAllKeysExhaustedError/
      DIDRequestError (or if neither pool is configured at all), builds a
      clip from the presenter's own photo held static for the real
      narration audio's own duration, with that audio baked in
      (build_static_fallback_clip) — added 2026-08-20 after confirming
      live that the original fallback (a shared, content-independent,
      SILENT generic placeholder) meant the hook's narration was audible
      nowhere in the final video whenever both real tiers failed. That
      original generic placeholder (ensure_fallback_asset,
      fallback_assets/generic_hook.mp4) is kept as a last-resort safety
      net for if even the photo/audio inputs themselves are broken, and
      is still what a fresh checkout with zero API keys configured falls
      through to eventually.

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

# rendering/adapters/ffmpeg_video_renderer.py's TARGET_SIZE — kept as an
# independent constant (matching that module's own "self-contained
# provider" pattern) rather than importing across the providers/rendering
# boundary for one tuple.
_FALLBACK_CLIP_SIZE = (720, 1280)
MIN_AUDIO_FALLBACK_SECONDS = 0.5  # floor so a near-instant audio clip can't produce a ~0s video moviepy chokes on


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


def build_static_fallback_clip(image_path: str, audio_path: str, output_path: str | Path) -> str:
    """The Tier 3 fallback `generate_avatar_hook` actually reaches for
    when both Hedra and D-ID fail with a real image+audio in hand (the
    common case, not a missing-input edge case): the presenter's own
    photo held static for the ACTUAL narration audio's own duration, with
    that audio baked in — not `ensure_fallback_asset()`'s shared generic
    solid-color-and-SILENT placeholder.

    That silent placeholder is a fixed FALLBACK_DURATION_SECONDS (5s)
    regardless of how long the narration actually is, and — being
    content-independent by design, so it can be cached once and reused
    across every caller — has no way to carry per-call audio. The
    practical effect, confirmed live and repeatedly on 2026-08-20 (Hedra
    permanently rejecting its configured keys, D-ID timing out after
    ~4 minutes of retries): whenever both real tiers failed, the
    reference language's spoken hook was audible NOWHERE in the final
    video — a real quality gap, not a cosmetic one, on what is currently
    the common path. This function is called once per `generate_avatar_hook`
    invocation (not cached/shared), since each call's audio differs."""
    from moviepy import AudioFileClip, CompositeVideoClip, ImageClip

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    audio_clip = raw = frame = video = None
    try:
        audio_clip = AudioFileClip(audio_path)
        duration = max(audio_clip.duration, MIN_AUDIO_FALLBACK_SECONDS)

        raw = ImageClip(image_path)
        cover_scale = max(_FALLBACK_CLIP_SIZE[0] / raw.w, _FALLBACK_CLIP_SIZE[1] / raw.h)
        frame = raw.resized(cover_scale).with_duration(duration).with_position("center")

        video = (
            CompositeVideoClip([frame], size=_FALLBACK_CLIP_SIZE)
            .with_duration(duration)
            .with_audio(audio_clip)
            .with_fps(24)
        )
        video.write_videofile(
            str(output_path), fps=24, codec="libx264", audio_codec="aac", preset="ultrafast", logger=None,
        )
    finally:
        for clip in (video, frame, raw, audio_clip):
            if clip is None:
                continue
            try:
                clip.close()
            except Exception as exc:  # noqa: BLE001 - cleanup must never mask the real result/error
                logger.debug("build_static_fallback_clip: ignoring error closing %r: %s", clip, exc)
    return str(output_path)


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

        Worst-case wall-clock (ported from main's own note on this same
        method): each vendor tier retries every key in its pool, and each
        key's attempt can run all the way to that vendor's
        POLL_TIMEOUT_SECONDS (300s, both clients) before being counted as
        failed and moving on — there is no shared/aggregate deadline
        across the cascade. Theoretical worst case before falling through
        to Tier 3 is (num_hedra_keys x 300s) + (num_did_keys x 300s) — a
        known, accepted tradeoff, not something to redesign here.

        `aspect_ratio` (default "9:16", unchanged from this dashboard's
        existing portrait-hook behavior) is forwarded to Hedra only —
        D-ID's client has no aspect_ratio parameter, its output shape
        follows the source `image_path`'s own dimensions."""
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
            logger.error(
                "generate_avatar_hook: both Hedra and D-ID exhausted — building a static fallback "
                "from the presenter photo with the real narration audio baked in"
            )
            try:
                asset.storage_path = build_static_fallback_clip(
                    image_path, audio_path, VIDEO_DIR / f"{asset.id}_fallback.mp4",
                )
            except Exception as exc:  # noqa: BLE001 - the fallback of the fallback must never crash the demo
                logger.error(
                    "generate_avatar_hook: building the audio-matched fallback failed (%s) — "
                    "using the generic silent placeholder instead", exc,
                )
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
