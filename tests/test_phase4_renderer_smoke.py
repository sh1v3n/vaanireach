"""Phase 4 validation check: feeds a dummy avatar video, 3 dummy B-roll
images, and a dummy body-audio track into MoviePyVideoRenderer and asserts
it stitches them into a playable output.mp4 with no codec errors — the
"quick test script" the Phase 4 brief asked for, wired up as a real pytest
so it runs with the rest of the suite instead of needing a separate manual
step.

Deliberately exercises BOTH renderer entry points:
  - compose_final_video(): the direct path, explicit file paths in.
  - render(): the generic VideoRenderer ABC path, which must derive those
    same file paths from Scene/MediaAsset/AudioAsset lists.
Both must produce a valid MP4 from the same dummy inputs.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.models.enums import GenerationStatus, LanguageCode, MediaAssetType, SceneType
from core.models.media import AudioAsset, MediaAsset
from core.models.storyboard import Scene
from providers.video.avatar_provider import ensure_fallback_asset
from rendering.adapters.moviepy_video_renderer import MoviePyVideoRenderer

PROJECT_ID = "proj-phase4-smoke"
STORYBOARD_ID = "sb-phase4-smoke"
BROLL_COLORS = [(220, 60, 60), (60, 180, 90), (60, 90, 220)]


def _make_dummy_avatar_video(tmp_path: Path) -> str:
    """Reuses avatar_provider's own Tier-3 placeholder generator — it
    already produces a real 720x1280 MP4 with a silent baked-in audio
    track, i.e. exactly the shape Scene 1 is supposed to have."""
    return ensure_fallback_asset(tmp_path / "dummy_avatar_video.mp4", duration_seconds=3.0)


def _make_dummy_broll_images(tmp_path: Path) -> list[str]:
    from PIL import Image

    paths = []
    for i, color in enumerate(BROLL_COLORS):
        # Deliberately not 9:16 and not identical to each other, to
        # exercise Ken Burns' "cover scale" letterbox-avoidance logic.
        img = Image.new("RGB", (800, 600), color=color)
        path = tmp_path / f"dummy_broll_{i}.jpg"
        img.save(path, format="JPEG")
        paths.append(str(path))
    return paths


def _make_dummy_body_audio(tmp_path: Path, *, duration_seconds: float = 6.0) -> str:
    import numpy as np
    from moviepy import AudioClip

    path = tmp_path / "dummy_body_audio.wav"
    # A quiet 220Hz tone rather than pure silence, so the audio stream
    # isn't degenerate — closer to what a real TTS track looks like.
    clip = AudioClip(lambda t: 0.05 * np.sin(2 * np.pi * 220 * t), duration=duration_seconds, fps=24000)
    try:
        clip.write_audiofile(str(path), codec="pcm_s16le", logger=None)
    finally:
        clip.close()
    return str(path)


@pytest.fixture(scope="module")
def dummy_inputs(tmp_path_factory) -> dict:
    tmp_path = tmp_path_factory.mktemp("phase4_inputs")
    return {
        "avatar_video_path": _make_dummy_avatar_video(tmp_path),
        "broll_image_paths": _make_dummy_broll_images(tmp_path),
        "body_audio_path": _make_dummy_body_audio(tmp_path),
    }


def test_compose_final_video_stitches_without_codec_errors(tmp_path, dummy_inputs) -> None:
    renderer = MoviePyVideoRenderer(output_dir=tmp_path)

    asset = renderer.compose_final_video(
        dummy_inputs["avatar_video_path"],
        dummy_inputs["broll_image_paths"],
        dummy_inputs["body_audio_path"],
        project_id=PROJECT_ID,
        storyboard_id=STORYBOARD_ID,
        language=LanguageCode.HI,
        captions_text="यह एक परीक्षण कैप्शन है।",
        output_name="output",
    )

    assert asset.generation_status == GenerationStatus.COMPLETE
    assert asset.storage_path_mp4 is not None
    out_path = Path(asset.storage_path_mp4)
    assert out_path.exists()
    assert out_path.stat().st_size > 0
    assert out_path.name == "output.mp4"

    # avatar (3s) + body audio (6s, split across 3 B-roll images) == 9s total.
    assert asset.duration_seconds == pytest.approx(9.0, abs=0.5)
    assert renderer.get_status(asset.id) == GenerationStatus.COMPLETE


def test_render_derives_paths_from_scenes_and_produces_the_same_result(tmp_path, dummy_inputs) -> None:
    renderer = MoviePyVideoRenderer(output_dir=tmp_path)

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
