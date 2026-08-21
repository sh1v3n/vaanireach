"""FfmpegVideoRenderer — the guaranteed VideoRenderer implementation
(Phase 2 media plan): FFmpeg CLI via subprocess. FFmpeg is the one hard
system dependency this pipeline has (verified in Step A —
`brew install ffmpeg`, confirmed callable from Python's `subprocess`),
so this renderer has no fallback: if ffmpeg itself is broken, there is
no lower tier to drop to.

Distinct from rendering/adapters/moviepy_video_renderer.py (Phase 3/4's
MoviePy-based renderer, backed by MoviePy's own bundled imageio-ffmpeg
binary, built for the avatar-hook + B-roll assembly sequence). This one
targets the guaranteed pipeline's generic scene-image + scene-audio +
caption composition and calls the system ffmpeg directly.

Currently implements single-scene composition only (Step D scope) —
`render()` (the generic multi-scene VideoRenderer ABC entry point) and
`export_captions()` (script/translation-level, multi-scene caption
export) are intentionally not implemented yet; multi-scene composition
is explicitly deferred to a later step.
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from core.models.enums import GenerationStatus, LanguageCode, TransitionType
from core.models.media import AudioAsset, MediaAsset
from core.models.script import Script
from core.models.storyboard import Scene
from core.models.translation import Translation
from core.models.media import VideoAsset
from rendering.adapters.caption_burner import CAPTION_BAR_HEIGHT
from rendering.interfaces.video_renderer import VideoRenderer

logger = logging.getLogger("vaanireach.rendering.ffmpeg_video_renderer")

VIDEO_OUTPUT_DIR = Path(os.environ.get("RENDERED_VIDEO_OUTPUT_DIR", "./data/video/final"))

FRAME_RATE = 25
PIP_WIDTH = 200
PIP_MARGIN = 16
PIP_RING_BORDER = 6
PIP_RING_COLOR = "0xc9a227"  # matches frontend/tailwind.config.ts's `gold` token

# Maps our TransitionType straight onto ffmpeg's built-in xfade
# transition names — no bespoke transition code, just the standard
# filter used as intended. CUT uses "fade" too, but with a duration so
# short (see _TRANSITION_BASE_DURATION) it reads as a hard cut, not a
# second visible transition style to build.
_XFADE_TRANSITION_NAME: dict[TransitionType, str] = {
    TransitionType.CUT: "fade",
    TransitionType.FADE: "fade",
    TransitionType.SLIDE: "slideleft",
    TransitionType.ZOOM: "zoomin",
}
_TRANSITION_BASE_DURATION: dict[TransitionType, float] = {
    TransitionType.CUT: 0.05,
    TransitionType.FADE: 0.5,
    TransitionType.SLIDE: 0.5,
    TransitionType.ZOOM: 0.5,
}
_MAX_TRANSITION_FRACTION = 0.3  # a transition may never eat more than 30% of either neighboring scene


def _format_srt_timestamp(seconds: float) -> str:
    """SRT timestamp: HH:MM:SS,mmm"""
    total_ms = round(seconds * 1000)
    hours, rem_ms = divmod(total_ms, 3_600_000)
    minutes, rem_ms = divmod(rem_ms, 60_000)
    secs, ms = divmod(rem_ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def build_scene_srt(narration_text: str, *, start_seconds: float, duration_seconds: float) -> str:
    """Builds a single-cue SRT block covering exactly [start_seconds,
    start_seconds + duration_seconds] — i.e. the caption is on screen for
    precisely as long as the scene's own (audio-derived) duration, per
    the "audio duration is the source of truth for scene timing" rule."""
    end_seconds = start_seconds + duration_seconds
    return (
        f"1\n"
        f"{_format_srt_timestamp(start_seconds)} --> {_format_srt_timestamp(end_seconds)}\n"
        f"{narration_text}\n"
    )


def _format_vtt_timestamp(seconds: float) -> str:
    """WebVTT timestamp: HH:MM:SS.mmm (dot, not comma, is the only
    difference from SRT)."""
    return _format_srt_timestamp(seconds).replace(",", ".")


def build_multi_scene_captions(scenes: list[Scene]) -> tuple[str, str]:
    """Builds SRT + VTT covering the whole storyboard: one cue per scene,
    cumulative timestamps derived from each scene's own (real,
    TTS-measured) duration_seconds — never estimated, never independent
    of actual narration timing. Returns (srt_text, vtt_text)."""
    srt_lines: list[str] = []
    vtt_lines: list[str] = ["WEBVTT", ""]
    cursor = 0.0
    for i, scene in enumerate(scenes, start=1):
        start, end = cursor, cursor + scene.duration_seconds
        srt_lines.append(
            f"{i}\n{_format_srt_timestamp(start)} --> {_format_srt_timestamp(end)}\n"
            f"{scene.narration_segment_text}\n"
        )
        vtt_lines.append(
            f"{_format_vtt_timestamp(start)} --> {_format_vtt_timestamp(end)}\n"
            f"{scene.narration_segment_text}\n"
        )
        cursor = end
    return "\n".join(srt_lines), "\n".join(vtt_lines)


def concat_audio_files(audio_paths: list[str], tmp_path: Path) -> Path:
    """Sequential audio concat — no crossfade, no gaps. Total duration
    is exactly the sum of the input files' own durations.

    Re-encodes through a filter graph rather than the concat demuxer's
    `-c copy` stream-copy path — found by direct reproduction that
    stream-copying inputs with DIFFERENT sample rates/channel counts
    (e.g. SarvamTTSProvider's 24kHz mono output mixed with its own
    edge-tts fallback's 44.1kHz stereo output) silently produces a
    corrupted, wrong-duration result with no error. The
    `aresample`+`concat` filter graph normalizes every input to a common
    format before concatenating, so this is correct regardless of which
    TTS vendor served which scene.

    Module-level (not a FfmpegVideoRenderer method) so callers besides
    compose_multi_scene can reuse it — e.g. rendering/multilingual_video.py
    builds the same full-narration audio track this way to feed the
    avatar lip-sync provider."""
    out_path = tmp_path / "audio_concat.wav"
    n = len(audio_paths)

    inputs: list[str] = []
    for p in audio_paths:
        inputs += ["-i", p]

    per_input_filters = "".join(
        f"[{i}:a]aresample=48000,aformat=sample_fmts=s16:channel_layouts=stereo[a{i}];"
        for i in range(n)
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
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"audio concat failed: {result.stderr}")
    return out_path


class FfmpegVideoRenderer(VideoRenderer):
    def __init__(self, output_dir: Path | str | None = None) -> None:
        self.output_dir = Path(output_dir) if output_dir is not None else VIDEO_OUTPUT_DIR
        self._job_status: dict[str, GenerationStatus] = {}

    # ---------------------------------------------------------------- single-scene (Step D)

    def compose_single_scene(
        self,
        *,
        image_path: str,
        audio_path: str,
        narration_text: str,
        project_id: str,
        storyboard_id: str = "single-scene",
    ) -> VideoAsset:
        """Composes exactly one scene image + one audio track into one
        MP4: the image is held static for the full audio duration (audio
        is authoritative — `-shortest` against a `-loop 1` infinite image
        source means the audio track, not an arbitrary default, is what
        ends the video). No captions are burned in; export separately via
        build_scene_srt()."""
        self.output_dir.mkdir(parents=True, exist_ok=True)

        video_asset = VideoAsset(
            project_id=project_id,
            storyboard_id=storyboard_id,
            language=self._infer_language_or_default(),
            generation_status=GenerationStatus.IN_PROGRESS,
        )
        out_path = self.output_dir / f"{video_asset.id}.mp4"

        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-framerate", "25", "-i", image_path,
            "-i", audio_path,
            "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(out_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            self._job_status[video_asset.id] = GenerationStatus.FAILED
            raise RuntimeError(f"ffmpeg composition failed (exit {result.returncode}): {result.stderr}")

        video_asset.storage_path_mp4 = str(out_path)
        video_asset.renderer_name = "ffmpeg-subprocess"
        video_asset.generation_status = GenerationStatus.COMPLETE
        self._job_status[video_asset.id] = GenerationStatus.COMPLETE
        return video_asset

    @staticmethod
    def _infer_language_or_default():
        from core.models.enums import LanguageCode
        return LanguageCode.EN

    # ---------------------------------------------------------------- multi-scene (Step E)

    def compose_multi_scene(
        self,
        *,
        scenes: list[Scene],
        image_paths: list[str],
        audio_paths: list[str],
        project_id: str,
        storyboard_id: str = "multi-scene",
        width: int = 720,
        height: int = 1280,
    ) -> VideoAsset:
        """Composes N scenes into ONE MP4, in narrative order, each with
        its own Ken-Burns pan/zoom motion (via ffmpeg's zoompan filter)
        and a real xfade crossfade into the next scene per its
        transition_to_next_scene (CUT/FADE/SLIDE/ZOOM). Audio is kept
        strictly sequential (never crossfaded) — overlapping two
        different narration lines would produce garbled speech, so only
        the VISUAL track blends at transitions; the audio track is the
        exact, uncompressed concatenation of every scene's real TTS
        duration, and video is padded (via the last scene's clip) to
        match that same total exactly, so narration is never cut short."""
        if len(scenes) != len(image_paths) or len(scenes) != len(audio_paths):
            raise ValueError("scenes, image_paths, and audio_paths must be the same length")
        if not scenes:
            raise ValueError("compose_multi_scene: scenes is empty")

        self.output_dir.mkdir(parents=True, exist_ok=True)
        durations = [s.duration_seconds for s in scenes]
        n = len(scenes)

        with tempfile.TemporaryDirectory(prefix="ffmpeg_multiscene_") as tmp:
            tmp_path = Path(tmp)

            # 1. transition durations per boundary, clamped so a
            # transition never eats more than _MAX_TRANSITION_FRACTION
            # of either neighboring scene's own duration.
            transition_durations: list[float] = []
            for i in range(n - 1):
                ttype = scenes[i].transition_to_next_scene or TransitionType.CUT
                base = _TRANSITION_BASE_DURATION[ttype]
                cap = _MAX_TRANSITION_FRACTION * min(durations[i], durations[i + 1])
                transition_durations.append(min(base, cap))
            total_compression = sum(transition_durations)

            # 2. render each scene's own silent Ken-Burns clip. The LAST
            # scene's clip is padded by total_compression so the fully
            # xfade-chained result lands exactly on sum(durations).
            clip_paths: list[Path] = []
            for i, (scene, image_path, duration) in enumerate(zip(scenes, image_paths, durations)):
                clip_duration = duration + (total_compression if i == n - 1 else 0.0)
                clip_path = tmp_path / f"clip_{i}.mp4"
                self._render_zoompan_clip(image_path, clip_duration, width, height, clip_path)
                clip_paths.append(clip_path)

            # 3. chain via xfade, tracking cumulative duration as we go.
            video_only_path = self._xfade_chain(clip_paths, scenes, durations, transition_durations, tmp_path)

            # 4. audio: simple sequential concat, untouched, total = sum(durations).
            audio_concat_path = concat_audio_files(audio_paths, tmp_path)

            # 5. mux
            video_asset = VideoAsset(
                project_id=project_id,
                storyboard_id=storyboard_id,
                language=self._infer_language_or_default(),
                generation_status=GenerationStatus.IN_PROGRESS,
            )
            out_path = self.output_dir / f"{video_asset.id}.mp4"
            target_duration = sum(durations)
            cmd = [
                "ffmpeg", "-y",
                "-i", str(video_only_path), "-i", str(audio_concat_path),
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-t", f"{target_duration:.3f}",
                str(out_path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                self._job_status[video_asset.id] = GenerationStatus.FAILED
                raise RuntimeError(f"ffmpeg final mux failed (exit {result.returncode}): {result.stderr}")

        video_asset.storage_path_mp4 = str(out_path)
        video_asset.renderer_name = "ffmpeg-subprocess"
        video_asset.duration_seconds = target_duration
        video_asset.generation_status = GenerationStatus.COMPLETE
        self._job_status[video_asset.id] = GenerationStatus.COMPLETE
        return video_asset

    @staticmethod
    def _render_zoompan_clip(image_path: str, duration: float, width: int, height: int, out_path: Path) -> None:
        """One scene's silent Ken-Burns clip: a gentle continuous zoom-in
        on the still image, held for exactly `duration` seconds."""
        num_frames = max(1, round(duration * FRAME_RATE))
        zoompan = f"zoompan=z='min(zoom+0.0008,1.15)':d={num_frames}:s={width}x{height}:fps={FRAME_RATE}"
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-loop", "1", "-i", image_path,
            "-t", f"{duration:.3f}",
            "-vf", zoompan,
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(out_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(f"zoompan clip render failed for {image_path}: {result.stderr}")

    @staticmethod
    def _xfade_chain(
        clip_paths: list[Path], scenes: list[Scene], durations: list[float],
        transition_durations: list[float], tmp_path: Path,
    ) -> Path:
        n = len(clip_paths)
        out_path = tmp_path / "video_chain.mp4"
        if n == 1:
            # nothing to chain — re-encode isn't needed, just use the clip directly.
            return clip_paths[0]

        inputs: list[str] = []
        for clip_path in clip_paths:
            inputs += ["-i", str(clip_path)]

        filter_parts: list[str] = []
        running_label = "0:v"
        running_duration = durations[0]
        for i in range(1, n):
            d = transition_durations[i - 1]
            transition_name = _XFADE_TRANSITION_NAME[scenes[i - 1].transition_to_next_scene or TransitionType.CUT]
            offset = max(0.0, running_duration - d)
            out_label = f"v{i}" if i < n - 1 else "vout"
            filter_parts.append(
                f"[{running_label}][{i}:v]xfade=transition={transition_name}:duration={d:.3f}:offset={offset:.3f}[{out_label}]"
            )
            # The last clip was already padded by total_compression at render time (see caller).
            # Use the clip's *actual* probed duration (not the target `durations[i]`) so the
            # running offset stays exact even with zoompan's own small frame-rounding.
            running_duration = running_duration + FfmpegVideoRenderer._probe_duration(clip_paths[i]) - d
            running_label = out_label

        filter_complex = ";".join(filter_parts)
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", f"[{running_label}]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(out_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"xfade chain failed: {result.stderr}")
        return out_path

    @staticmethod
    def _probe_duration(path: Path) -> float:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=15,
        )
        return float(result.stdout.strip())

    # ---------------------------------------------------------------- avatar PiP + caption burn-in

    def compose_pip_and_captions(
        self,
        *,
        broll_video_path: str,
        avatar_clip_path: str,
        caption_track_path: str,
        duration_seconds: float,
        project_id: str,
        storyboard_id: str,
        language: LanguageCode,
    ) -> VideoAsset:
        """Composites an existing B-roll+audio video (compose_multi_scene's
        own output) with a looping avatar PiP bubble (bottom-left, above
        the caption bar) and a burned-in caption track (rendering/adapters/
        caption_burner.py), in one ffmpeg pass. `-stream_loop -1` on the
        avatar input means a clip shorter than `duration_seconds` (e.g.
        the Tier-3 static fallback) loops seamlessly to cover the whole
        video rather than leaving a blank corner; a real Hedra/D-ID clip
        (audio-driven, already matching `duration_seconds`) effectively
        never repeats. `-t duration_seconds` caps the final output so
        neither a looped avatar nor a slightly-longer (frame-quantized)
        caption track can extend it.

        The avatar is masked to a circle (not the old square/rectangular
        box) with a thin gold ring border, matching the frontend's navy/
        gold theme — a video-call-style bubble rather than a floating
        cutout. Built via ffmpeg's geq filter: alpha is set to 255 inside
        the circle (distance from center <= radius) and 0 outside it,
        applied to both the avatar footage (cropped to a square first,
        force_original_aspect_ratio=increase + centered crop, so a
        non-square source portrait doesn't distort into an ellipse) and
        a solid-gold circle rendered as the ring behind it."""
        self.output_dir.mkdir(parents=True, exist_ok=True)

        video_asset = VideoAsset(
            project_id=project_id, storyboard_id=storyboard_id, language=language,
            generation_status=GenerationStatus.IN_PROGRESS,
        )
        out_path = self.output_dir / f"{video_asset.id}.mp4"

        ring_diameter = PIP_WIDTH + PIP_RING_BORDER * 2
        circle_alpha = (
            "geq=lum='p(X,Y)':a='if(gt(pow(X-{c}\\,2)+pow(Y-{c}\\,2)\\,pow({c}\\,2))\\,0\\,255)'"
        )
        filter_complex = (
            f"color=c={PIP_RING_COLOR}:size={ring_diameter}x{ring_diameter}:duration={duration_seconds:.3f}:"
            f"rate={FRAME_RATE},format=yuva420p,{circle_alpha.format(c=ring_diameter / 2)}[ring];"
            f"[1:v]scale={PIP_WIDTH}:{PIP_WIDTH}:force_original_aspect_ratio=increase,crop={PIP_WIDTH}:{PIP_WIDTH},"
            f"format=yuva420p,{circle_alpha.format(c=PIP_WIDTH / 2)}[avt];"
            f"[ring][avt]overlay=x={PIP_RING_BORDER}:y={PIP_RING_BORDER}:shortest=0[avt_ring];"
            f"[0:v][avt_ring]overlay=x={PIP_MARGIN}:y=H-{CAPTION_BAR_HEIGHT}-h-{PIP_MARGIN}:shortest=0[v1];"
            f"[v1][2:v]overlay=x=0:y=0:shortest=0[vout]"
        )
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-i", broll_video_path,
            "-stream_loop", "-1", "-i", avatar_clip_path,
            "-i", caption_track_path,
            "-filter_complex", filter_complex,
            "-map", "[vout]", "-map", "0:a",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-t", f"{duration_seconds:.3f}",
            str(out_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            self._job_status[video_asset.id] = GenerationStatus.FAILED
            raise RuntimeError(f"compose_pip_and_captions: ffmpeg composite failed (exit {result.returncode}): {result.stderr}")

        video_asset.storage_path_mp4 = str(out_path)
        video_asset.renderer_name = "ffmpeg-subprocess-pip-captions"
        video_asset.duration_seconds = duration_seconds
        video_asset.generation_status = GenerationStatus.COMPLETE
        self._job_status[video_asset.id] = GenerationStatus.COMPLETE
        return video_asset

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
        raise NotImplementedError(
            "FfmpegVideoRenderer.render: the generic ABC entry point (deriving paths from "
            "MediaAsset/AudioAsset lists) isn't wired up — use compose_single_scene (one scene) "
            "or compose_multi_scene (N scenes, explicit image/audio paths) directly instead."
        )

    def export_captions(self, script: Script, translation: Translation | None, format: str) -> str:
        raise NotImplementedError(
            "FfmpegVideoRenderer.export_captions: Script alone doesn't carry per-scene timing "
            "— use build_scene_srt (one scene) or build_multi_scene_captions (N scenes, needs "
            "the actual Scene list) directly instead."
        )

    def get_status(self, job_id: str) -> GenerationStatus:
        status = self._job_status.get(job_id)
        if status is None:
            logger.warning("get_status: unknown job_id %s", job_id)
            return GenerationStatus.FAILED
        return status
