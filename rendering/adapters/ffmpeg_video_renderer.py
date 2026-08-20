"""FfmpegVideoRenderer — the video-generation mechanics (frame format,
avatar box, captions) ported from `main`'s `rendering/adapters/
ffmpeg_video_renderer.py` + `caption_burner.py` (a separately-built,
TemplateStoryDirector-driven pipeline), adapted onto THIS branch's own
orchestration (dashboard/app.py's Groq fact-extraction/script-generation/
verification pipeline, one narration script + N B-roll images per
language) rather than replacing it. See dashboard/app.py's module
docstring and this repo's ADR-005 for why a renderer swap, not a
pipeline swap.

Replaces `MoviePyVideoRenderer` (rendering/adapters/moviepy_video_renderer.py,
now retired): same `compose_final_video()`/`render()`/`export_captions()`/
`get_status()` surface, so dashboard/app.py only needed an import swap —
the fact-extraction/scripting/verification/TTS layers above it are
untouched. What actually changed, following `main`'s lead exactly:

  1. FORMAT: ffmpeg CLI (zoompan Ken Burns + xfade transitions) instead
     of per-frame MoviePy compositing — same 720x1280 vertical output,
     same "hook lead-in + evenly-sliced body segments" B-roll structure
     this branch already used, just assembled by `ffmpeg` subprocess
     calls instead of Python-side frame math. Requires system ffmpeg +
     ffprobe (this environment's PATH now has both — see the ffmpeg
     install this session's chat history).
  2. AVATAR: a small (PIP_WIDTH=200px, matching main's own revert-to-
     "small box, bottom-left, plain background" commit exactly), plain
     rectangular corner overlay — NOT the old chroma-keyed/drop-shadowed
     hero overlay. That old approach tried to key the presenter's flat
     photo backdrop out to blend into the B-roll; when a vendor's
     watermark (D-ID's tiled "D-iD" mark, confirmed live via
     .audit/03-video-hook.png) corrupted the backdrop's uniform color,
     the mask failed and the watermark rode straight through, worse than
     the plain-rectangle problem it was trying to solve. A small,
     honestly-a-box PiP in the corner (how a real news broadcast inset
     actually looks) is robust to that failure mode instead of fighting
     it — and per the same structural choice this branch already made,
     it's only composited for the 5-second hook segment, not looped over
     the whole video (main's avatar narrates the ENTIRE video, so it
     loops its clip to cover that; this branch's avatar is a hook-only
     device, so its clip already IS the right length once fitted).
     NOTE: this does not itself detect or strip a vendor watermark —
     main's own avatar_provider.py has no such detection either (see its
     module docstring's account-blocker notes); "port from main" ported
     main's actual mitigation (small, non-blended box), not a watermark
     detector that doesn't exist on either branch yet.
  3. CAPTIONS: short, timed multi-cue captions (rendering/adapters/
     caption_burner.py, max 2 lines each, dark bottom gradient card,
     correct Devanagari/Bengali glyph support) instead of one giant
     caption bar holding the entire narration for the whole B-roll
     segment. `caption_burner.split_narration_into_cues()` segments this
     branch's single narration string into per-cue text+duration slices
     (main gets this for free from its own per-Scene narration list;
     this branch has to derive it, since it has one narration script per
     language, not N).
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from core.models.enums import GenerationStatus, LanguageCode, MediaAssetType, SceneType
from core.models.media import AudioAsset, MediaAsset, VideoAsset
from core.models.script import Script
from core.models.storyboard import Scene
from core.models.translation import Translation
from rendering.adapters.caption_burner import (
    CAPTION_BAR_HEIGHT,
    build_caption_track,
    split_narration_into_cues,
)
from rendering.interfaces.video_renderer import VideoRenderer

logger = logging.getLogger("vaanireach.rendering.ffmpeg_video_renderer")

VIDEO_OUTPUT_DIR = Path(os.environ.get("RENDERED_VIDEO_OUTPUT_DIR", "./data/video/final"))

TARGET_SIZE = (720, 1280)  # unchanged from the old MoviePy renderer / main's own portrait revert
FRAME_RATE = 24

KEN_BURNS_ZOOM_EXPR = "min(zoom+0.0008,1.15)"  # ported verbatim from main's _render_zoompan_clip
MIN_SCENE_DURATION_SECONDS = 0.5  # floor so a very short audio track can't produce a ~0s clip ffmpeg chokes on

# News-anchor picture-in-picture, small and corner-anchored — see module
# docstring point 2. Matches main's own constants exactly (its
# "Revert to original portrait video: small box, bottom-left, plain
# background" commit).
PIP_WIDTH = 200
PIP_MARGIN = 16

# A short, constant crossfade between every B-roll segment (this branch's
# Scenes carry no per-boundary TransitionType the way main's
# TemplateStoryDirector output does, so one consistent fade stands in for
# main's per-scene-role transition table) — never more than
# _MAX_TRANSITION_FRACTION of either neighboring segment's own duration.
TRANSITION_DURATION_SECONDS = 0.5
_MAX_TRANSITION_FRACTION = 0.3

ENCODE_CODEC = "libx264"
ENCODE_AUDIO_CODEC = "aac"
ENCODE_PRESET = "ultrafast"  # matches the old renderer's own choice — a hackathon demo, not archival encoding

_PROBE_TIMEOUT_SECONDS = 15
_RENDER_TIMEOUT_SECONDS = 120
_MUX_TIMEOUT_SECONDS = 120


def _run(cmd: list[str], *, timeout: float, step: str) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"{step}: ffmpeg failed (exit {result.returncode}): {result.stderr[-2000:]}")


def _probe_duration(path: str | Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=_PROBE_TIMEOUT_SECONDS,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"_probe_duration: ffprobe failed for {path}: {result.stderr[-500:]}")
    return float(result.stdout.strip())


def _even_durations(total: float, count: int) -> list[float]:
    """Splits `total` into `count` slices summing to exactly `total`, the
    last slice absorbing any rounding remainder — identical to the old
    MoviePy renderer's helper of the same name, so the B-roll body
    segment timing this branch already relied on is unchanged."""
    if count <= 0:
        raise ValueError("_even_durations: count must be positive")
    each = total / count
    durations = [each] * count
    durations[-1] = total - each * (count - 1)
    return durations


def _render_zoompan_clip(image_path: str, duration: float, out_path: Path) -> None:
    """One image's silent Ken Burns clip — ported from main's
    `_render_zoompan_clip` (a gentle continuous zoom-in, held for exactly
    `duration` seconds). `-vf scale=...:force_original_aspect_ratio=increase,crop=...`
    first covers TARGET_SIZE regardless of the source image's own aspect
    ratio (main's images always match; this branch's Cloudflare-generated
    B-roll doesn't guarantee it), matching the old MoviePy renderer's own
    "cover, don't letterbox" behavior."""
    duration = max(duration, MIN_SCENE_DURATION_SECONDS)
    width, height = TARGET_SIZE
    num_frames = max(1, round(duration * FRAME_RATE))
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},"
        f"zoompan=z='{KEN_BURNS_ZOOM_EXPR}':d={num_frames}:s={width}x{height}:fps={FRAME_RATE}"
    )
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-loop", "1", "-i", image_path,
        "-t", f"{duration:.3f}",
        "-vf", vf,
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    _run(cmd, timeout=_RENDER_TIMEOUT_SECONDS, step=f"_render_zoompan_clip({image_path})")


def _xfade_chain(clip_paths: list[Path], durations: list[float], out_path: Path) -> None:
    """Chains N silent Ken Burns clips via ffmpeg's `xfade` filter —
    ported from main's `_xfade_chain`, simplified to one constant
    transition style/duration (see TRANSITION_DURATION_SECONDS above)
    since this branch's B-roll segments carry no per-boundary transition
    metadata the way main's Scene list does."""
    n = len(clip_paths)
    if n == 1:
        import shutil

        shutil.copyfile(clip_paths[0], out_path)
        return

    transition_durations = [
        min(TRANSITION_DURATION_SECONDS, _MAX_TRANSITION_FRACTION * min(durations[i], durations[i + 1]))
        for i in range(n - 1)
    ]

    inputs: list[str] = []
    for p in clip_paths:
        inputs += ["-i", str(p)]

    filter_parts: list[str] = []
    running_label = "0:v"
    running_duration = durations[0]
    for i in range(1, n):
        d = transition_durations[i - 1]
        offset = max(0.0, running_duration - d)
        out_label = f"v{i}" if i < n - 1 else "vout"
        filter_parts.append(
            f"[{running_label}][{i}:v]xfade=transition=fade:duration={d:.3f}:offset={offset:.3f}[{out_label}]"
        )
        running_duration = running_duration + durations[i] - d
        running_label = out_label

    cmd = [
        "ffmpeg", "-y", "-v", "error",
        *inputs,
        "-filter_complex", ";".join(filter_parts),
        "-map", f"[{running_label}]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    _run(cmd, timeout=_RENDER_TIMEOUT_SECONDS, step="_xfade_chain")


def _concat_audio_files(audio_paths: list[str], out_path: Path) -> None:
    """Sequential audio concat, no crossfade/gaps — ported verbatim from
    main's `concat_audio_files` (module-level there so
    rendering/multilingual_video.py could reuse it for the avatar
    lip-sync track too; kept as a private helper here since this branch
    has no equivalent second caller). Re-encodes through an
    aresample+aformat+concat filter graph rather than the concat
    demuxer's `-c copy` path — the stream-copy path silently produces a
    corrupted, wrong-duration result when inputs differ in sample
    rate/channel count (e.g. SarvamTTSProvider's 24kHz mono vs. its own
    edge-tts fallback's 44.1kHz stereo), confirmed on main by direct
    reproduction."""
    n = len(audio_paths)
    inputs: list[str] = []
    for p in audio_paths:
        inputs += ["-i", p]

    per_input_filters = "".join(
        f"[{i}:a]aresample=48000,aformat=sample_fmts=s16:channel_layouts=stereo[a{i}];" for i in range(n)
    )
    concat_inputs = "".join(f"[a{i}]" for i in range(n))
    filter_complex = f"{per_input_filters}{concat_inputs}concat=n={n}:v=0:a=1[out]"

    cmd = [
        "ffmpeg", "-y", "-v", "error",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[out]",
        str(out_path),
    ]
    _run(cmd, timeout=_MUX_TIMEOUT_SECONDS, step="_concat_audio_files")


def _extract_audio(video_path: str, out_path: Path) -> None:
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", video_path, "-vn", "-ar", "48000", "-ac", "2", str(out_path)]
    _run(cmd, timeout=_RENDER_TIMEOUT_SECONDS, step="_extract_audio")


def _fit_video_to_duration(video_path: str, target_duration: float, out_path: Path) -> None:
    """Trims or freeze-extends a (silent, visual-only) clip to exactly
    `target_duration` — the ffmpeg-CLI equivalent of the old MoviePy
    renderer's `_fit_video_to_duration`/`vfx.Freeze`. Used when reusing
    ONE shared avatar clip across languages (see compose_final_video's
    `hook_audio_path` param): a non-reference language's own hook audio
    rarely runs exactly as long as the video's original lip-synced
    motion. Shorter target: a straight trim. Longer target: `tpad`'s
    `stop_mode=clone` freezes on the last frame for the remainder rather
    than looping the talking motion from the start, which would read as
    an obvious stutter."""
    source_duration = _probe_duration(video_path)
    if source_duration >= target_duration:
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-i", video_path, "-t", f"{target_duration:.3f}", "-an",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(out_path),
        ]
    else:
        pad = target_duration - source_duration
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-i", video_path,
            "-vf", f"tpad=stop_mode=clone:stop_duration={pad:.3f}",
            "-t", f"{target_duration:.3f}", "-an",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(out_path),
        ]
    _run(cmd, timeout=_RENDER_TIMEOUT_SECONDS, step="_fit_video_to_duration")


class FfmpegVideoRenderer(VideoRenderer):
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
        hook_audio_path: str | None = None,
    ) -> VideoAsset:
        """Same external contract as the old MoviePyVideoRenderer.compose_final_video
        (dashboard/app.py calls this exact signature) — see the module
        docstring for what changed internally. Raises rather than
        degrading — a broken final render has no meaningful fallback to
        substitute, matching every VideoRenderer this codebase has had."""
        if not broll_image_paths:
            raise ValueError("compose_final_video: broll_image_paths is empty")

        self.output_dir.mkdir(parents=True, exist_ok=True)
        asset = VideoAsset(
            project_id=project_id,
            storyboard_id=storyboard_id,
            language=language,
            renderer_name="ffmpeg-subprocess",
            generation_status=GenerationStatus.IN_PROGRESS,
        )
        self._job_status[asset.id] = GenerationStatus.IN_PROGRESS
        out_path = self.output_dir / f"{output_name or asset.id}.mp4"

        try:
            with tempfile.TemporaryDirectory(prefix="ffmpeg_compose_") as tmp:
                tmp_path = Path(tmp)
                width, height = TARGET_SIZE

                # 1. hook duration + the avatar clip fitted (trimmed/frozen) to it.
                if hook_audio_path is not None:
                    hook_duration = max(_probe_duration(hook_audio_path), MIN_SCENE_DURATION_SECONDS)
                    hook_audio_for_concat = hook_audio_path
                else:
                    hook_duration = max(_probe_duration(avatar_video_path), MIN_SCENE_DURATION_SECONDS)
                    hook_audio_for_concat = str(tmp_path / "hook_audio_extracted.wav")
                    _extract_audio(avatar_video_path, Path(hook_audio_for_concat))

                avatar_fitted_path = tmp_path / "avatar_fitted.mp4"
                _fit_video_to_duration(avatar_video_path, hook_duration, avatar_fitted_path)

                body_duration = _probe_duration(body_audio_path)

                # 2. B-roll background: hook lead-in (first image) + evenly-sliced body segments, Ken Burns + xfade.
                body_durations = _even_durations(body_duration, len(broll_image_paths))
                segment_durations = [hook_duration, *body_durations]
                clip_paths: list[Path] = []
                for i, (image_path, duration) in enumerate(zip(broll_image_paths, segment_durations)):
                    clip_path = tmp_path / f"broll_clip_{i}.mp4"
                    _render_zoompan_clip(image_path, duration, clip_path)
                    clip_paths.append(clip_path)
                background_path = tmp_path / "background.mp4"
                _xfade_chain(clip_paths, segment_durations, background_path)

                # 3. full audio: hook audio + body audio, concatenated.
                full_audio_path = tmp_path / "full_audio.wav"
                _concat_audio_files([hook_audio_for_concat, body_audio_path], full_audio_path)

                total_duration = hook_duration + body_duration

                # 4. caption cues spanning the whole timeline.
                caption_input: list[str] = []
                if captions_text and captions_text.strip():
                    cues = split_narration_into_cues(captions_text, total_duration)
                    caption_track_path = build_caption_track(
                        cues, language=language, width=width, height=height, tmp_dir=tmp_path,
                    )
                    caption_input = ["-i", str(caption_track_path)]

                # 5. final composite: background + avatar PiP (hook window only) [+ captions] + full audio.
                pip_output_label = "v1" if caption_input else "vout"
                filter_parts = [
                    f"[1:v]scale={PIP_WIDTH}:-2[avt]",
                    f"[0:v][avt]overlay=x={PIP_MARGIN}:y=H-{CAPTION_BAR_HEIGHT}-h-{PIP_MARGIN}:"
                    f"enable='lte(t,{hook_duration:.3f})'[{pip_output_label}]",
                ]
                video_label = pip_output_label
                if caption_input:
                    filter_parts.append(f"[{video_label}][2:v]overlay=x=0:y=0[vout]")
                    video_label = "vout"

                cmd = [
                    "ffmpeg", "-y", "-v", "error",
                    "-i", str(background_path),
                    "-i", str(avatar_fitted_path),
                    *caption_input,
                    "-i", str(full_audio_path),
                    "-filter_complex", ";".join(filter_parts),
                    "-map", f"[{video_label}]",
                    "-map", f"{3 if caption_input else 2}:a",
                    "-c:v", ENCODE_CODEC, "-pix_fmt", "yuv420p",
                    "-c:a", ENCODE_AUDIO_CODEC, "-b:a", "192k",
                    "-preset", ENCODE_PRESET,
                    "-t", f"{total_duration:.3f}",
                    str(out_path),
                ]
                _run(cmd, timeout=_MUX_TIMEOUT_SECONDS, step="compose_final_video: final composite")

            asset.storage_path_mp4 = str(out_path)
            asset.duration_seconds = total_duration
            asset.generation_status = GenerationStatus.COMPLETE
            self._job_status[asset.id] = GenerationStatus.COMPLETE
            return asset
        except Exception:
            self._job_status[asset.id] = GenerationStatus.FAILED
            asset.generation_status = GenerationStatus.FAILED
            raise

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
        """Identical adapter logic to the old MoviePyVideoRenderer.render():
        exactly one Scene with scene_type==AVATAR is the hook, every other
        scene (ordered by order_index) is a B-roll slot, each scene's
        rendered MediaAsset is looked up by scene_id, and the body
        narration audio is whichever AudioAsset isn't scoped to the
        avatar scene."""
        if transitions:
            logger.info("render: transitions=%r requested but not yet implemented by FfmpegVideoRenderer", transitions)
        if branding:
            logger.info("render: branding=%r requested but not yet implemented by FfmpegVideoRenderer", branding)

        avatar_scenes = [s for s in scenes if s.scene_type == SceneType.AVATAR]
        if len(avatar_scenes) != 1:
            raise ValueError(
                f"render: expected exactly one AVATAR scene (the hook), found {len(avatar_scenes)} — "
                "FfmpegVideoRenderer.render() only implements the hook+B-roll assembly sequence; "
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
            raise ValueError("render: no body-narration AudioAsset found (expected one not scoped to the AVATAR scene)")
        body_audio = body_audio_candidates[0]

        return self.compose_final_video(
            avatar_asset.storage_path,  # type: ignore[arg-type]
            broll_image_paths,  # type: ignore[arg-type]
            body_audio.storage_path,  # type: ignore[arg-type]
            project_id=avatar_asset.project_id,
            storyboard_id=avatar_scene.storyboard_id,
            language=body_audio.language,
            captions_text=captions,
        )

    def export_captions(self, script: Script, translation: Translation | None, format: str) -> str:
        """Real multi-cue SRT/VTT, built from the same
        `split_narration_into_cues()` segmentation actually burned into
        the video — an upgrade over the old renderer's single-cue,
        whole-script placeholder (which its own docstring flagged as one,
        for lack of per-word timing data; this branch's cue splitter now
        exists, so the exported captions match what's on screen)."""
        fmt = format.strip().lower()
        if fmt not in ("srt", "vtt"):
            raise ValueError(f"export_captions: unsupported format {format!r} — expected 'srt' or 'vtt'")

        text = (translation.translated_narration_text if translation else script.narration_text).strip()
        cues = split_narration_into_cues(text, float(script.target_duration_seconds))

        lines: list[str] = [] if fmt == "srt" else ["WEBVTT", ""]
        cursor = 0.0
        for i, (cue_text, duration) in enumerate(cues, start=1):
            start, end = cursor, cursor + duration
            if fmt == "srt":
                lines.append(f"{i}\n{_format_timestamp(start, fmt)} --> {_format_timestamp(end, fmt)}\n{cue_text}\n")
            else:
                lines.append(f"{_format_timestamp(start, fmt)} --> {_format_timestamp(end, fmt)}\n{cue_text}\n")
            cursor = end
        return "\n".join(lines)

    def get_status(self, job_id: str) -> GenerationStatus:
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
