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


def test_avatar_pip_is_circular_not_rectangular(tmp_path):
    """Real regression guard for the circle-not-square/rectangle PiP
    shape: composites a solid-red avatar onto a solid-green B-roll and
    checks actual pixel colors in the extracted frame — the PiP
    bounding box's CORNER must show the B-roll's green showing through
    (the old rectangular box would show red there), the box's CENTER
    must show the avatar's red, and a pixel right at the circle's edge
    must show the gold ring border — not just "ffmpeg exited 0"."""
    from PIL import Image

    from rendering.adapters.ffmpeg_video_renderer import (
        CAPTION_BAR_HEIGHT, PIP_MARGIN, PIP_RING_BORDER, PIP_WIDTH,
    )

    broll_path = tmp_path / "green_broll.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "color=0x00ff00:s=720x1280:d=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(broll_path)],
        check=True, timeout=30,
    )
    avatar_path = tmp_path / "red_avatar.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=0xff0000:s=300x300:d=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(avatar_path)],
        check=True, timeout=30,
    )
    caption_path = _make_dummy_caption_track(tmp_path, duration=2.0)

    renderer = FfmpegVideoRenderer(output_dir=tmp_path / "out")
    video_asset = renderer.compose_pip_and_captions(
        broll_video_path=str(broll_path), avatar_clip_path=str(avatar_path), caption_track_path=caption_path,
        duration_seconds=2.0, project_id=PROJECT_ID, storyboard_id=STORYBOARD_ID, language=LanguageCode.EN,
    )

    frame_path = tmp_path / "frame.png"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", video_asset.storage_path_mp4, "-frames:v", "1", str(frame_path)],
        check=True, timeout=30,
    )
    img = Image.open(frame_path).convert("RGB")

    # The PiP bounding box, in absolute frame coordinates (bottom-left,
    # above the caption bar) — mirrors compose_pip_and_captions' own
    # overlay math (y=H-CAPTION_BAR_HEIGHT-h-PIP_MARGIN, x=PIP_MARGIN).
    ring_diameter = PIP_WIDTH + PIP_RING_BORDER * 2
    box_x0 = PIP_MARGIN
    box_y0 = 1280 - CAPTION_BAR_HEIGHT - ring_diameter - PIP_MARGIN
    center = (box_x0 + ring_diameter // 2, box_y0 + ring_diameter // 2)
    corner = (box_x0 + 2, box_y0 + 2)  # just inside the bounding box's actual corner
    edge = (box_x0 + ring_diameter // 2, box_y0 + 2)  # top-center — right on the ring

    def _is_close(pixel: tuple[int, int, int], target: tuple[int, int, int], tol: int = 40) -> bool:
        return all(abs(a - b) <= tol for a, b in zip(pixel, target))

    assert _is_close(img.getpixel(corner), (0, 255, 0)), (
        f"corner pixel {img.getpixel(corner)} should show the green B-roll through the masked-out "
        "corner — a rectangular PiP box would show red/gold there instead"
    )
    assert _is_close(img.getpixel(center), (255, 0, 0)), f"center pixel {img.getpixel(center)} should be the red avatar"
    assert _is_close(img.getpixel(edge), (0xc9, 0xa2, 0x27)), f"edge pixel {img.getpixel(edge)} should be the gold ring"
