"""caption_burner: Pillow-rendered caption frames + a qtrle alpha video
track, composited by FfmpegVideoRenderer.compose_pip_and_captions
(Task 5) — see docs/superpowers/specs/2026-08-20-video-captions-avatar-shortening-design.md
for why this doesn't use ffmpeg's subtitles/drawtext filters."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from core.models.enums import LanguageCode, NarrativeRole, SceneType, TransitionType  # noqa: E402
from core.models.storyboard import Scene  # noqa: E402
from rendering.adapters.caption_burner import (  # noqa: E402
    CAPTION_BAR_HEIGHT, build_caption_track, font_for_language, render_caption_frame,
)


def _scene(text: str, duration: float, order: int = 0) -> Scene:
    return Scene(
        storyboard_id="caption-test", order_index=order, scene_type=SceneType.TEXT,
        narrative_role=NarrativeRole.BENEFIT, narration_segment_text=text, duration_seconds=duration,
    )


def test_font_for_language_maps_hindi_and_marathi_to_devanagari():
    from rendering.adapters.caption_burner import _DEVANAGARI_FONT, _LATIN_FONT
    assert font_for_language(LanguageCode.HI) == _DEVANAGARI_FONT
    assert font_for_language(LanguageCode.MR) == _DEVANAGARI_FONT
    assert font_for_language(LanguageCode.EN) == _LATIN_FONT


def test_font_for_language_falls_back_to_latin_for_unbundled_scripts():
    from rendering.adapters.caption_burner import _LATIN_FONT
    assert font_for_language(LanguageCode.TA) == _LATIN_FONT  # Tamil not bundled - documented fallback


def test_render_caption_frame_is_transparent_except_the_bar():
    from rendering.adapters.caption_burner import _LATIN_FONT
    img = render_caption_frame("Hello world", width=720, font_path=_LATIN_FONT)
    assert img.size == (720, CAPTION_BAR_HEIGHT)
    assert img.mode == "RGBA"
    # top-left corner is inside the semi-opaque bar (bar fills the whole frame) - alpha must be > 0
    assert img.getpixel((0, 0))[3] > 0


def test_render_caption_frame_renders_devanagari_without_error():
    from rendering.adapters.caption_burner import _DEVANAGARI_FONT
    img = render_caption_frame("किसान सहायता योजना जाहीर करत आहोत", width=720, font_path=_DEVANAGARI_FONT)
    assert img.size == (720, CAPTION_BAR_HEIGHT)


def test_build_caption_track_duration_matches_sum_of_scene_durations(tmp_path):
    scenes = [_scene("First line of narration.", 2.0, 0), _scene("Second, slightly longer line here.", 3.0, 1)]
    track_path = build_caption_track(scenes, language=LanguageCode.EN, width=720, height=1280, tmp_dir=tmp_path)
    assert track_path.exists()

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(track_path)],
        capture_output=True, text=True, timeout=30,
    )
    assert probe.returncode == 0, probe.stderr
    data = json.loads(probe.stdout)
    stream = data["streams"][0]
    assert stream["width"] == 720
    assert stream["height"] == 1280
    assert stream["pix_fmt"] == "argb"
    actual_duration = float(data["format"]["duration"])
    assert actual_duration == pytest.approx(5.0, abs=0.2)  # 2.0 + 3.0, +/- frame-rate quantization


def test_build_caption_track_single_scene_skips_concat(tmp_path):
    """One scene needs no concat step - the single cue clip is used directly."""
    scenes = [_scene("Only one line.", 2.0, 0)]
    track_path = build_caption_track(scenes, language=LanguageCode.EN, width=720, height=1280, tmp_dir=tmp_path)
    assert track_path.exists()
