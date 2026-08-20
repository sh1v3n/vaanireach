"""Step D (Phase 2 media plan): compose ONE scene's PNG + its TTS audio +
one caption line into a single valid MP4 via FfmpegVideoRenderer.
Exercises the real, freshly-generated Step B (EdgeTtsProvider) and Step C
(HtmlSceneRenderer) artifacts — not stale files from earlier runs — so
this test's pass/fail reflects the current code, not disk leftovers.

Requirements checked (per the Step D approval message):
1/2. Real Step C PNG + real Step B WAV are used as inputs.
3.   FfmpegVideoRenderer (subprocess ffmpeg, not moviepy's bundled one).
4/5. Audio duration determines video duration; visual holds for the
     full audio duration (video duration == audio duration, not the
     other way around).
6.   One valid SRT caption, from the narration + actual timing.
7.   One real MP4 is produced.
8.   ffprobe verification: video stream, audio stream, duration,
     resolution, codecs.
9.   The MP4 is actually decoded end-to-end (ffmpeg null-muxer decode)
     and a frame is extracted and inspected as an image.
10.  SRT timestamps fall within the video's actual duration.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from core.models.enums import LanguageCode  # noqa: E402
from providers.tts.edge_tts_provider import EdgeTtsProvider  # noqa: E402
from rendering.adapters.html_scene_renderer import HtmlSceneRenderer, VIEWPORT_WIDTH, VIEWPORT_HEIGHT  # noqa: E402
from rendering.adapters.ffmpeg_video_renderer import FfmpegVideoRenderer, build_scene_srt  # noqa: E402
from tests.test_scene_renderer_step_c import make_scene  # noqa: E402

NARRATION = "This scheme provides a subsidy of five thousand rupees."


def _ffprobe(path: str) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"ffprobe failed: {result.stderr}"
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def composed(tmp_path_factory) -> dict:
    tmp_path = tmp_path_factory.mktemp("step_d")

    # --- 1/2: real, freshly-generated inputs (not stale disk leftovers) ---
    tts = EdgeTtsProvider()
    audio_asset = tts.synthesize(NARRATION, LanguageCode.EN, project_id="step-d-single-scene")
    assert audio_asset.storage_path is not None
    assert audio_asset.duration_seconds is not None and audio_asset.duration_seconds > 0

    scene = make_scene()
    assert scene.narration_segment_text == NARRATION  # same text as the audio, by construction
    renderer = HtmlSceneRenderer()
    image_asset = renderer.render_scene(scene)
    assert image_asset.storage_path is not None

    # --- 3/4/5/7: FfmpegVideoRenderer, audio-duration-authoritative composition ---
    video_renderer = FfmpegVideoRenderer(output_dir=tmp_path)
    video_asset = video_renderer.compose_single_scene(
        image_path=image_asset.storage_path,
        audio_path=audio_asset.storage_path,
        narration_text=scene.narration_segment_text,
        project_id="step-d-single-scene",
    )

    # --- 6: SRT ---
    srt_text = build_scene_srt(scene.narration_segment_text, start_seconds=0.0, duration_seconds=audio_asset.duration_seconds)

    probe = _ffprobe(video_asset.storage_path_mp4)

    return {
        "audio_asset": audio_asset,
        "image_asset": image_asset,
        "video_asset": video_asset,
        "srt_text": srt_text,
        "probe": probe,
        "tmp_path": tmp_path,
    }


def test_mp4_file_exists_and_is_nonempty(composed):
    path = Path(composed["video_asset"].storage_path_mp4)
    assert path.exists()
    assert path.stat().st_size > 1000  # a real MP4, not an empty/truncated file


def test_video_and_audio_streams_present_with_valid_codecs(composed):
    streams = composed["probe"]["streams"]
    video_streams = [s for s in streams if s["codec_type"] == "video"]
    audio_streams = [s for s in streams if s["codec_type"] == "audio"]
    assert len(video_streams) == 1, f"expected exactly one video stream, got {video_streams!r}"
    assert len(audio_streams) == 1, f"expected exactly one audio stream, got {audio_streams!r}"
    assert video_streams[0]["codec_name"] == "h264"
    assert audio_streams[0]["codec_name"] == "aac"


def test_resolution_matches_the_scene_image(composed):
    video_stream = next(s for s in composed["probe"]["streams"] if s["codec_type"] == "video")
    assert video_stream["width"] == VIEWPORT_WIDTH
    assert video_stream["height"] == VIEWPORT_HEIGHT


def test_video_duration_matches_audio_duration_not_some_other_value(composed):
    """Requirement 4/5: audio duration determines video duration — the
    visual must hold for the FULL audio duration, not be cut short or
    padded to an arbitrary/default length."""
    audio_duration = composed["audio_asset"].duration_seconds
    video_duration = float(composed["probe"]["format"]["duration"])
    assert video_duration == pytest.approx(audio_duration, abs=0.15), (
        f"video duration {video_duration:.3f}s does not match audio duration {audio_duration:.3f}s"
    )


def test_mp4_decodes_end_to_end_without_errors(composed):
    """Automated stand-in for "actually play the file" in a headless
    environment: force ffmpeg to decode every frame of both streams to
    the null muxer — any codec/container corruption surfaces as a
    non-zero exit or stderr output, not just a superficially-valid header."""
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", composed["video_asset"].storage_path_mp4, "-f", "null", "-"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"ffmpeg decode failed: {result.stderr}"
    assert result.stderr.strip() == "", f"ffmpeg reported decode errors: {result.stderr}"


def test_extracted_frame_is_a_real_readable_image(composed):
    """Further playback verification: pull an actual frame out of the
    middle of the video and confirm it's genuinely decodable image data
    (not just that ffprobe parsed a header)."""
    from PIL import Image

    frame_path = composed["tmp_path"] / "frame.png"
    mid_time = composed["audio_asset"].duration_seconds / 2
    result = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", str(mid_time),
         "-i", composed["video_asset"].storage_path_mp4, "-frames:v", "1", str(frame_path)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"frame extraction failed: {result.stderr}"
    with Image.open(frame_path) as img:
        img.verify()
    with Image.open(frame_path) as img:
        assert img.size == (VIEWPORT_WIDTH, VIEWPORT_HEIGHT)


def test_srt_is_valid_and_within_video_duration(composed):
    srt_text = composed["srt_text"]
    video_duration = float(composed["probe"]["format"]["duration"])

    # valid SRT block shape: index, timestamp line, text, blank line
    match = re.match(
        r"1\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n(.+)\n",
        srt_text,
    )
    assert match, f"SRT does not match expected single-cue format:\n{srt_text!r}"
    start_str, end_str, text = match.groups()
    assert text == NARRATION

    def to_seconds(ts: str) -> float:
        h, m, rest = ts.split(":")
        s, ms = rest.split(",")
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000

    start_s, end_s = to_seconds(start_str), to_seconds(end_str)
    assert 0.0 <= start_s < end_s
    assert end_s <= video_duration + 0.05, (
        f"SRT end timestamp {end_s:.3f}s exceeds video duration {video_duration:.3f}s"
    )
