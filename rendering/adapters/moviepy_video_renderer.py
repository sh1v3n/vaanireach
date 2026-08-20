"""MoviePyVideoRenderer — concrete `VideoRenderer` (see ADR-005) built on
MoviePy v2 (>=2.0 renamed the old chainable `.set_x()`/`.fx()` API to
`with_x()`/direct effect methods like `.resized()`/`.cropped()` — the same
v2-only surface Phase 3's avatar_provider.py and Phase 2's
sarvam_tts_provider.py already depend on via `with_fps`/`with_audio`/
`subclipped`). Backed by the imageio-ffmpeg binary MoviePy installs
automatically, so no system `ffmpeg` install is required — the same
reasoning documented in avatar_provider.py's fallback-asset generator.

The Assembly Sequence this implements (per docs/workflow.md's Video
Composition stage):
  1. Scene 1 (the hook) = the avatar clip from Phase 3
     (providers/video/avatar_provider.py's `generate_avatar_hook`) — its
     narration audio is already baked in, so it is used as-is.
  2. Scene 2 (the B-roll) = the Gemini/Imagen images from Phase 4's
     GeminiImagenProvider, each given a subtle Ken Burns zoom and a
     duration of `body_audio.duration / len(images)`.
  3. The B-roll sequence's audio track is the Phase 2 `body_audio.wav`.
  4. An optional caption bar burns the translated script text in over
     the B-roll.

`compose_final_video()` is the primary, fully-typed entry point (explicit
file paths in, one VideoAsset out) — exactly what a media agent or the
Phase 4 validation script calls. `render()` implements the generic Phase 0
`VideoRenderer` ABC by deriving those same file paths from its flatter
`scenes` / `audio_assets` / `visual_assets` lists (documented below) and
delegating to `compose_final_video()`.
"""
from __future__ import annotations

import logging
import os
import textwrap
from pathlib import Path
from typing import Any

from core.models.enums import GenerationStatus, LanguageCode, MediaAssetType, SceneType
from core.models.media import AudioAsset, MediaAsset, VideoAsset
from core.models.script import Script
from core.models.storyboard import Scene
from core.models.translation import Translation
from rendering.interfaces.video_renderer import VideoRenderer

logger = logging.getLogger("vaanireach.rendering.moviepy_video_renderer")

VIDEO_OUTPUT_DIR = Path(os.environ.get("RENDERED_VIDEO_OUTPUT_DIR", "./data/video/final"))

# Vertical short-form, matching the avatar hook clip's resolution
# (providers/video/avatar_provider.py's ColorClip(size=(720, 1280))) so
# Scene 1 and the B-roll sequence concatenate without letterboxing.
TARGET_SIZE = (720, 1280)
DEFAULT_FPS = 24

KEN_BURNS_ZOOM_FRACTION = 0.12  # subtle: the image grows 12% over its full on-screen duration
MIN_SCENE_DURATION_SECONDS = 0.5  # floor so a very short body_audio track can't produce a ~0s clip moviepy chokes on

CAPTION_BAR_HEIGHT = 180
CAPTION_FONT_SIZE = 34
CAPTION_MAX_CHARS_PER_LINE = 36
CAPTION_MAX_LINES = 3
_CAPTION_BG_RGBA = (0, 0, 0, 165)
_CAPTION_FG_RGB = (255, 255, 255)
_CAPTION_FONT_CANDIDATES = (
    "arial.ttf",  # resolved via PIL's font search path; also the literal Windows font name
    "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)

# Optimized for a hackathon demo, not archival quality — see the Phase 4
# brief: ultrafast trades file size/compression efficiency for encode
# speed, which is what matters when re-rendering repeatedly against a demo
# clock.
ENCODE_CODEC = "libx264"
ENCODE_AUDIO_CODEC = "aac"
ENCODE_PRESET = "ultrafast"


def _close_all(*clips: Any) -> None:
    """Best-effort cleanup — a clip failing to close (e.g. because it was
    never fully opened) must never mask the real error/result."""
    for clip in clips:
        if clip is None:
            continue
        try:
            clip.close()
        except Exception as exc:  # noqa: BLE001 - cleanup must never raise
            logger.debug("_close_all: ignoring error closing %r: %s", clip, exc)


def _resolve_caption_font(size: int):
    from PIL import ImageFont

    for candidate in _CAPTION_FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    logger.warning(
        "_resolve_caption_font: no TrueType font found among %r — falling back to PIL's tiny default bitmap font",
        _CAPTION_FONT_CANDIDATES,
    )
    return ImageFont.load_default()


def _build_caption_clip(text: str, *, width: int, duration: float):
    """Renders a semi-transparent bar with wrapped white text via Pillow
    and hands it to moviepy as an RGBA ImageClip — deliberately avoiding
    moviepy's own TextClip, which depends on either ImageMagick being
    installed or a specific font file being resolvable, neither of which
    this project wants as a hard prerequisite (the same "don't require a
    system dependency we can't verify is installed" reasoning as
    ADR-005's ffmpeg note)."""
    import numpy as np
    from moviepy import ImageClip
    from PIL import Image, ImageDraw

    wrapped_lines = textwrap.wrap(text.strip(), width=CAPTION_MAX_CHARS_PER_LINE) or [""]
    if len(wrapped_lines) > CAPTION_MAX_LINES:
        wrapped_lines = wrapped_lines[:CAPTION_MAX_LINES]
        wrapped_lines[-1] = wrapped_lines[-1].rstrip() + "…"
    wrapped_text = "\n".join(wrapped_lines)

    img = Image.new("RGBA", (width, CAPTION_BAR_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, width, CAPTION_BAR_HEIGHT], fill=_CAPTION_BG_RGBA)

    font = _resolve_caption_font(CAPTION_FONT_SIZE)
    bbox = draw.multiline_textbbox((0, 0), wrapped_text, font=font, spacing=8, align="center")
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = max(0, (width - text_w) // 2)
    y = max(0, (CAPTION_BAR_HEIGHT - text_h) // 2)
    draw.multiline_text((x, y), wrapped_text, font=font, fill=_CAPTION_FG_RGB, spacing=8, align="center")

    return ImageClip(np.array(img), duration=duration)


def _apply_ken_burns(image_path: str, duration: float, *, size: tuple[int, int] = TARGET_SIZE):
    """The Ken Burns effect: scale the source image up to fully cover
    `size` (a "cover" fit, so no letterboxing regardless of the source
    image's aspect ratio), then grow it further by
    `KEN_BURNS_ZOOM_FRACTION` over the clip's duration. Composited onto a
    fixed-size canvas so the frame dimensions stay constant even as the
    underlying image grows — CompositeVideoClip simply doesn't draw the
    parts of an oversized, centered clip that fall outside its canvas."""
    from moviepy import CompositeVideoClip, ImageClip

    duration = max(duration, MIN_SCENE_DURATION_SECONDS)
    raw = ImageClip(image_path)
    cover_scale = max(size[0] / raw.w, size[1] / raw.h)
    base = raw.resized(cover_scale).with_duration(duration)

    zoomed = base.resized(lambda t: 1.0 + KEN_BURNS_ZOOM_FRACTION * (t / duration))
    zoomed = zoomed.with_position("center")

    return CompositeVideoClip([zoomed], size=size).with_duration(duration)


def _even_durations(total: float, count: int) -> list[float]:
    """Splits `total` into `count` slices that sum to exactly `total`
    (floating-point exact, not just "close enough") — per the Phase 4
    brief: "divide the audio duration by 3 ... duration for each B-roll
    image", with the last slice absorbing any rounding remainder so the
    concatenated B-roll sequence's duration exactly matches the audio."""
    if count <= 0:
        raise ValueError("_even_durations: count must be positive")
    each = total / count
    durations = [each] * count
    durations[-1] = total - each * (count - 1)
    return durations


class MoviePyVideoRenderer(VideoRenderer):
    def __init__(self, output_dir: str | Path | None = None) -> None:
        self.output_dir = Path(output_dir) if output_dir is not None else VIDEO_OUTPUT_DIR
        self._job_status: dict[str, GenerationStatus] = {}

    # ---------------------------------------------------------------- the assembly sequence

    def compose_final_video(
        self,
        avatar_video_path: str,
        broll_image_paths: list[str],
        body_audio_path: str,
        *,
        project_id: str,
        storyboard_id: str,
        language: LanguageCode,
        captions_text: str | None = None,
        output_name: str | None = None,
    ) -> VideoAsset:
        """Scene 1 (avatar hook, audio already baked in) + Scene 2 (Ken
        Burns B-roll, `body_audio_path` overlaid, each image's duration =
        len(body_audio) / len(broll_image_paths)) -> one MP4. Raises
        rather than degrading — unlike the provider layer, a broken final
        render has no meaningful fallback to substitute."""
        if not broll_image_paths:
            raise ValueError("compose_final_video: broll_image_paths is empty")

        self.output_dir.mkdir(parents=True, exist_ok=True)
        asset = VideoAsset(
            project_id=project_id,
            storyboard_id=storyboard_id,
            language=language,
            renderer_name="moviepy",
            generation_status=GenerationStatus.IN_PROGRESS,
        )
        self._job_status[asset.id] = GenerationStatus.IN_PROGRESS
        out_path = self.output_dir / f"{output_name or asset.id}.mp4"

        avatar_clip = broll_clips = broll_sequence = body_audio_clip = caption_clip = final = None
        try:
            from moviepy import AudioFileClip, CompositeVideoClip, VideoFileClip, concatenate_videoclips

            avatar_clip = VideoFileClip(avatar_video_path)
            body_audio_clip = AudioFileClip(body_audio_path)

            durations = _even_durations(body_audio_clip.duration, len(broll_image_paths))
            broll_clips = [
                _apply_ken_burns(path, dur, size=TARGET_SIZE)
                for path, dur in zip(broll_image_paths, durations)
            ]
            broll_sequence = concatenate_videoclips(broll_clips, method="compose").with_audio(body_audio_clip)

            final = concatenate_videoclips([avatar_clip, broll_sequence], method="compose")

            if captions_text and captions_text.strip():
                caption_clip = (
                    _build_caption_clip(captions_text, width=TARGET_SIZE[0], duration=broll_sequence.duration)
                    .with_position(("center", "bottom"))
                    .with_start(avatar_clip.duration)
                )
                final = CompositeVideoClip([final, caption_clip], size=TARGET_SIZE)

            final.write_videofile(
                str(out_path),
                fps=DEFAULT_FPS,
                codec=ENCODE_CODEC,
                audio_codec=ENCODE_AUDIO_CODEC,
                preset=ENCODE_PRESET,
                logger=None,
            )

            asset.storage_path_mp4 = str(out_path)
            asset.duration_seconds = final.duration
            asset.generation_status = GenerationStatus.COMPLETE
            self._job_status[asset.id] = GenerationStatus.COMPLETE
            return asset
        except Exception:
            self._job_status[asset.id] = GenerationStatus.FAILED
            asset.generation_status = GenerationStatus.FAILED
            raise
        finally:
            _close_all(avatar_clip, *(broll_clips or []), broll_sequence, body_audio_clip, caption_clip, final)

    # ---------------------------------------------------------------- VideoRenderer ABC

    def render(
        self,
        scenes: list[Scene],
        audio_assets: list[AudioAsset],
        captions: str | None,
        visual_assets: list[MediaAsset],
        transitions: list[str],
        branding: dict[str, Any] | None,
    ) -> VideoAsset:
        """Adapts the generic Phase 0 ABC shape onto `compose_final_video`.
        The ABC gives flat lists keyed only by scene_id/asset_type, not an
        explicit "this is the hook, this is the B-roll" role, so that
        mapping is derived the same way the rest of the pipeline is
        expected to produce it: exactly one Scene with
        scene_type==AVATAR is the hook (Scene 1); every other scene,
        ordered by order_index, is a B-roll slot (Scene 2); each scene's
        rendered MediaAsset is looked up by scene_id in `visual_assets`;
        and the body narration audio is whichever AudioAsset is NOT
        scoped to the avatar scene (the avatar scene's own narration is
        already baked into its video clip by Phase 3 — see
        providers/video/avatar_provider.py).

        `transitions` and `branding` are accepted per the ABC but this MVP
        renderer doesn't yet act on them — logged rather than silently
        dropped so a caller passing them isn't misled into thinking they
        took effect.
        """
        if transitions:
            logger.info("render: transitions=%r requested but not yet implemented by MoviePyVideoRenderer", transitions)
        if branding:
            logger.info("render: branding=%r requested but not yet implemented by MoviePyVideoRenderer", branding)

        avatar_scenes = [s for s in scenes if s.scene_type == SceneType.AVATAR]
        if len(avatar_scenes) != 1:
            raise ValueError(
                f"render: expected exactly one AVATAR scene (the hook), found {len(avatar_scenes)} — "
                "MoviePyVideoRenderer.render() only implements the hook+B-roll assembly sequence; "
                "call compose_final_video() directly for full control over an arbitrary scene list."
            )
        avatar_scene = avatar_scenes[0]
        broll_scenes = sorted((s for s in scenes if s.id != avatar_scene.id), key=lambda s: s.order_index)
        if not broll_scenes:
            raise ValueError("render: no B-roll scenes found alongside the AVATAR hook scene")

        assets_by_scene: dict[str, list[MediaAsset]] = {}
        for asset in visual_assets:
            if asset.scene_id:
                assets_by_scene.setdefault(asset.scene_id, []).append(asset)

        def _first_asset(scene_id: str, asset_type: MediaAssetType) -> MediaAsset:
            for asset in assets_by_scene.get(scene_id, []):
                if asset.asset_type == asset_type and asset.storage_path:
                    return asset
            raise ValueError(f"render: no {asset_type.value} asset with a storage_path for scene {scene_id}")

        avatar_asset = _first_asset(avatar_scene.id, MediaAssetType.VIDEO_CLIP)
        broll_image_paths = [_first_asset(s.id, MediaAssetType.IMAGE).storage_path for s in broll_scenes]

        body_audio_candidates = [a for a in audio_assets if a.scene_id != avatar_scene.id and a.storage_path]
        if not body_audio_candidates:
            raise ValueError(
                "render: no body-narration AudioAsset found (expected one not scoped to the AVATAR scene)"
            )
        body_audio = body_audio_candidates[0]

        return self.compose_final_video(
            avatar_asset.storage_path,  # type: ignore[arg-type]  # _first_asset already checked storage_path is truthy
            broll_image_paths,  # type: ignore[arg-type]
            body_audio.storage_path,  # type: ignore[arg-type]
            project_id=avatar_asset.project_id,
            storyboard_id=avatar_scene.storyboard_id,
            language=body_audio.language,
            captions_text=captions,
        )

    def export_captions(self, script: Script, translation: Translation | None, format: str) -> str:
        """Naive single-cue caption spanning the script's full target
        duration — Phase 0 has no per-word/per-scene timing data to build
        real multi-cue captions from, so this is documented as a
        placeholder rather than faking precision it doesn't have."""
        fmt = format.strip().lower()
        if fmt not in ("srt", "vtt"):
            raise ValueError(f"export_captions: unsupported format {format!r} — expected 'srt' or 'vtt'")

        text = (translation.translated_narration_text if translation else script.narration_text).strip()
        end = _format_timestamp(script.target_duration_seconds, fmt)
        start = _format_timestamp(0, fmt)

        if fmt == "srt":
            return f"1\n{start} --> {end}\n{text}\n"
        return f"WEBVTT\n\n{start} --> {end}\n{text}\n"

    def get_status(self, job_id: str) -> GenerationStatus:
        """compose_final_video() is fully synchronous — it only returns
        once the MP4 has been written or raised — so there is no real
        async job to poll. An unrecognized id is reported FAILED rather
        than raising, matching every other provider in this codebase."""
        status = self._job_status.get(job_id)
        if status is None:
            logger.warning("get_status: unknown job_id %s", job_id)
            return GenerationStatus.FAILED
        return status


def _format_timestamp(seconds: float, fmt: str) -> str:
    seconds = max(0.0, seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    millis = int(round((secs - int(secs)) * 1000))
    sep = "," if fmt == "srt" else "."
    return f"{int(hours):02d}:{int(minutes):02d}:{int(secs):02d}{sep}{millis:03d}"
