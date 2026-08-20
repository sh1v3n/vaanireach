"""Renderer smoke test: feeds a dummy avatar video, 3 dummy B-roll
images, and a dummy body-audio track into FfmpegVideoRenderer and asserts
it stitches them into a playable output.mp4 with no codec errors — the
"quick test script" the Phase 4 brief asked for, wired up as a real
pytest so it runs with the rest of the suite instead of needing a
separate manual step.

Rewritten for FfmpegVideoRenderer (rendering/adapters/ffmpeg_video_renderer.py,
which replaced MoviePyVideoRenderer this session — see that module's
docstring) — same test intent and dummy-input shapes as before, since the
renderer's external `compose_final_video`/`render()` contract didn't
change, just its internals. Needs real system ffmpeg/ffprobe on PATH
(no fallback, by design — see the renderer's own module docstring).

Deliberately exercises BOTH renderer entry points:
  - compose_final_video(): the direct path, explicit file paths in.
  - render(): the generic VideoRenderer ABC path, which must derive those
    same file paths from Scene/MediaAsset/AudioAsset lists.
Both must produce a valid MP4 from the same dummy inputs.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from core.models.enums import GenerationStatus, LanguageCode, MediaAssetType, SceneType
from core.models.media import AudioAsset, MediaAsset
from core.models.storyboard import Scene
from providers.video.avatar_provider import ensure_fallback_asset
from rendering.adapters.ffmpeg_video_renderer import FfmpegVideoRenderer

PROJECT_ID = "proj-phase4-smoke"
STORYBOARD_ID = "sb-phase4-smoke"
BROLL_COLORS = [(220, 60, 60), (60, 180, 90), (60, 90, 220)]

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FfmpegVideoRenderer requires system ffmpeg/ffprobe on PATH",
)


def _make_dummy_avatar_video(tmp_path: Path) -> str:
    """Reuses avatar_provider's own Tier-3 placeholder generator (moviepy-
    backed, unrelated to which VideoRenderer is under test) — it already
    produces a real 720x1280 MP4 with a silent baked-in audio track, i.e.
    exactly the shape the hook clip is supposed to have. Kept short
    (1.2s, not the original 3s): these are smoke tests ("does it stitch
    without codec errors"), not visual QA, and per-scene ffmpeg encoding
    time scales with clip count/duration."""
    return ensure_fallback_asset(tmp_path / "dummy_avatar_video.mp4", duration_seconds=1.2)


def _make_dummy_broll_images(tmp_path: Path) -> list[str]:
    from PIL import Image

    paths = []
    for i, color in enumerate(BROLL_COLORS):
        # Deliberately not 9:16 and not identical to each other, to
        # exercise the Ken Burns "cover" scale/crop logic.
        img = Image.new("RGB", (800, 600), color=color)
        path = tmp_path / f"dummy_broll_{i}.jpg"
        img.save(path, format="JPEG")
        paths.append(str(path))
    return paths


def _make_dummy_body_audio(tmp_path: Path, *, duration_seconds: float = 2.4, filename: str = "dummy_body_audio.wav") -> str:
    import numpy as np
    from moviepy import AudioClip

    path = tmp_path / filename
    # A quiet 220Hz tone rather than pure silence, so the audio stream
    # isn't degenerate — closer to what a real TTS track looks like.
    clip = AudioClip(lambda t: 0.05 * np.sin(2 * np.pi * 220 * t), duration=duration_seconds, fps=24000)
    try:
        clip.write_audiofile(str(path), codec="pcm_s16le", logger=None)
    finally:
        clip.close()
    return str(path)


def _probe(path: str) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-show_streams", "-of", "json", path],
        capture_output=True, text=True, timeout=30, check=True,
    )
    import json

    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def dummy_inputs(tmp_path_factory) -> dict:
    tmp_path = tmp_path_factory.mktemp("phase4_inputs")
    return {
        "avatar_video_path": _make_dummy_avatar_video(tmp_path),
        "broll_image_paths": _make_dummy_broll_images(tmp_path),
        "body_audio_path": _make_dummy_body_audio(tmp_path),
    }


def test_compose_final_video_stitches_without_codec_errors(tmp_path, dummy_inputs) -> None:
    renderer = FfmpegVideoRenderer(output_dir=tmp_path)

    asset = renderer.compose_final_video(
        dummy_inputs["avatar_video_path"],
        dummy_inputs["broll_image_paths"],
        dummy_inputs["body_audio_path"],
        project_id=PROJECT_ID,
        storyboard_id=STORYBOARD_ID,
        language=LanguageCode.HI,
        captions_text="यह एक परीक्षण कैप्शन है। यह लंबा है ताकि एक से अधिक कैप्शन खंड में विभाजित हो।",
        output_name="output",
    )

    assert asset.generation_status == GenerationStatus.COMPLETE
    assert asset.storage_path_mp4 is not None
    out_path = Path(asset.storage_path_mp4)
    assert out_path.exists()
    assert out_path.stat().st_size > 0
    assert out_path.name == "output.mp4"

    probe = _probe(str(out_path))
    video_streams = [s for s in probe["streams"] if s["codec_type"] == "video"]
    audio_streams = [s for s in probe["streams"] if s["codec_type"] == "audio"]
    assert video_streams and audio_streams
    assert int(video_streams[0]["width"]) == 720
    assert int(video_streams[0]["height"]) == 1280

    # avatar (1.2s) + body audio (2.4s, split across 3 B-roll images) == 3.6s total.
    assert asset.duration_seconds == pytest.approx(3.6, abs=0.4)
    assert float(probe["format"]["duration"]) == pytest.approx(3.6, abs=0.4)
    assert renderer.get_status(asset.id) == GenerationStatus.COMPLETE


@pytest.mark.parametrize(
    "override_hook_seconds",
    [
        0.6,  # shorter than the 1.2s dummy avatar video -> _fit_video_to_duration trims it
        2.0,  # longer than the 1.2s dummy avatar video -> _fit_video_to_duration freeze-extends it
    ],
)
def test_compose_final_video_hook_audio_override_swaps_audio_and_refits_video(
    tmp_path, dummy_inputs, override_hook_seconds,
) -> None:
    """Exercises the multi-language shared-video path: the same 1.2s
    dummy avatar clip, but with its baked-in audio replaced by a
    differently-timed hook track (as happens when a non-reference
    language's own narration runs a different length than the video it's
    reusing)."""
    renderer = FfmpegVideoRenderer(output_dir=tmp_path)
    override_hook_audio_path = _make_dummy_body_audio(
        tmp_path, duration_seconds=override_hook_seconds, filename="dummy_hook_override.wav",
    )

    asset = renderer.compose_final_video(
        dummy_inputs["avatar_video_path"],
        dummy_inputs["broll_image_paths"],
        dummy_inputs["body_audio_path"],
        project_id=PROJECT_ID,
        storyboard_id=STORYBOARD_ID,
        language=LanguageCode.MR,
        output_name=f"output_override_{override_hook_seconds}",
        hook_audio_path=override_hook_audio_path,
    )

    assert asset.generation_status == GenerationStatus.COMPLETE
    out_path = Path(asset.storage_path_mp4)
    assert out_path.exists() and out_path.stat().st_size > 0

    # overlay/background hook segment now follows the override audio's own
    # duration, not the original 1.2s avatar video's — total = hook + 2.4s body.
    expected_hook = max(override_hook_seconds, 0.5)  # MIN_SCENE_DURATION_SECONDS floor
    assert asset.duration_seconds == pytest.approx(expected_hook + 2.4, abs=0.4)


def test_render_derives_paths_from_scenes_and_produces_the_same_result(tmp_path, dummy_inputs) -> None:
    renderer = FfmpegVideoRenderer(output_dir=tmp_path)

    avatar_scene = Scene(
        storyboard_id=STORYBOARD_ID, order_index=0, scene_type=SceneType.AVATAR,
        narration_segment_text="hook narration", duration_seconds=3.0,
    )
    broll_scenes = [
        Scene(
            storyboard_id=STORYBOARD_ID, order_index=i + 1, scene_type=SceneType.IMAGE_MOTION,
            narration_segment_text=f"broll segment {i}", duration_seconds=2.0,
        )
        for i in range(len(dummy_inputs["broll_image_paths"]))
    ]

    avatar_media = MediaAsset(
        project_id=PROJECT_ID, scene_id=avatar_scene.id, asset_type=MediaAssetType.VIDEO_CLIP,
        storage_path=dummy_inputs["avatar_video_path"],
    )
    broll_media = [
        MediaAsset(project_id=PROJECT_ID, scene_id=scene.id, asset_type=MediaAssetType.IMAGE, storage_path=path)
        for scene, path in zip(broll_scenes, dummy_inputs["broll_image_paths"])
    ]
    body_audio = AudioAsset(
        project_id=PROJECT_ID, language=LanguageCode.HI, storage_path=dummy_inputs["body_audio_path"],
    )

    asset = renderer.render(
        scenes=[avatar_scene, *broll_scenes],
        audio_assets=[body_audio],
        captions=None,
        visual_assets=[avatar_media, *broll_media],
        transitions=[],
        branding=None,
    )

    assert asset.generation_status == GenerationStatus.COMPLETE
    assert Path(asset.storage_path_mp4).exists()
    assert asset.language == LanguageCode.HI
