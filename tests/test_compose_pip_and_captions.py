"""compose_pip_and_captions: composites a looping avatar PiP box +
burned-in caption track onto an existing B-roll video, in one ffmpeg
pass. Mirrors tests/test_phase4_renderer_smoke.py's dummy-input pattern."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from core.models.enums import GenerationStatus, LanguageCode  # noqa: E402
from providers.video.avatar_provider import ensure_fallback_asset  # noqa: E402
from rendering.adapters.ffmpeg_video_renderer import FfmpegVideoRenderer  # noqa: E402

PROJECT_ID = "proj-pip-captions-test"
STORYBOARD_ID = "sb-pip-captions-test"


def _make_dummy_broll(tmp_path: Path, *, duration: float = 5.0) -> str:
    out = tmp_path / "dummy_broll.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", f"color=green:s=720x1280:d={duration}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)],
        check=True, timeout=30,
    )
    return str(out)


def _make_dummy_caption_track(tmp_path: Path, *, duration: float = 5.0) -> str:
    from PIL import Image
    frame = Image.new("RGBA", (720, 1280), (0, 0, 0, 0))
    frame_path = tmp_path / "dummy_caption_frame.png"
    frame.save(frame_path)
    out = tmp_path / "dummy_caption_track.mov"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(frame_path),
         "-t", f"{duration}", "-pix_fmt", "yuva420p", "-c:v", "qtrle", str(out)],
        check=True, timeout=30,
    )
    return str(out)


@pytest.fixture(scope="module")
def composed(tmp_path_factory) -> dict:
    tmp_path = tmp_path_factory.mktemp("pip_captions")
    broll_path = _make_dummy_broll(tmp_path)
    # deliberately SHORTER than the broll (2s < 5s) - exercises the -stream_loop path
    avatar_path = ensure_fallback_asset(tmp_path / "dummy_avatar.mp4", duration_seconds=2.0)
    caption_path = _make_dummy_caption_track(tmp_path)

    renderer = FfmpegVideoRenderer(output_dir=tmp_path / "out")
    video_asset = renderer.compose_pip_and_captions(
        broll_video_path=broll_path, avatar_clip_path=avatar_path, caption_track_path=caption_path,
        duration_seconds=5.0, project_id=PROJECT_ID, storyboard_id=STORYBOARD_ID, language=LanguageCode.EN,
    )
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", video_asset.storage_path_mp4],
        capture_output=True, text=True, timeout=30,
    )
    assert probe.returncode == 0, probe.stderr
    return {"video_asset": video_asset, "probe": json.loads(probe.stdout)}


def test_video_asset_marked_complete(composed):
    assert composed["video_asset"].generation_status == GenerationStatus.COMPLETE
    assert composed["video_asset"].storage_path_mp4 is not None


def test_output_has_expected_dimensions_and_codecs(composed):
    streams = composed["probe"]["streams"]
    video_streams = [s for s in streams if s["codec_type"] == "video"]
    audio_streams = [s for s in streams if s["codec_type"] == "audio"]
    assert len(video_streams) == 1
    assert len(audio_streams) == 1
    assert video_streams[0]["width"] == 720
    assert video_streams[0]["height"] == 1280
    assert video_streams[0]["codec_name"] == "h264"
    assert audio_streams[0]["codec_name"] == "aac"


def test_output_duration_matches_target_despite_shorter_looped_avatar(composed):
    """The avatar clip is 2s, the target is 5s - -stream_loop must fill
    the gap rather than leaving the last 3s without a PiP box, and the
    final output must be capped at exactly the target, not the looped
    avatar's now-longer duration."""
    actual = float(composed["probe"]["format"]["duration"])
    assert actual == pytest.approx(5.0, abs=0.1)


def test_output_decodes_end_to_end_without_errors(composed):
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", composed["video_asset"].storage_path_mp4, "-f", "null", "-"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr.strip() == ""
