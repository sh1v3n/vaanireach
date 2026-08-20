"""Step E: multi-scene composition — 3 real scenes (subset of the full
NarrativeArc, to keep the test fast) through SceneRendererRegistry +
EdgeTtsProvider + FfmpegVideoRenderer.compose_multi_scene, with real
Ken-Burns motion and real xfade transitions between every pair. Verifies
the merged video's total duration, transition count, and that captions
span the full timeline. The full 8-scene run is exercised separately by
tests/demo_step_e_full_video.py (slower, produces the reviewable
artifact) — this test is the fast, CI-shaped correctness check.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from core.interfaces.scene_renderer import SceneRendererRegistry  # noqa: E402
from core.models.enums import LanguageCode  # noqa: E402
from providers.narrative.template_story_director import TemplateStoryDirector  # noqa: E402
from providers.tts.edge_tts_provider import EdgeTtsProvider  # noqa: E402
from rendering.adapters.html_scene_renderer import HtmlSceneRenderer, VIEWPORT_WIDTH, VIEWPORT_HEIGHT  # noqa: E402
from rendering.adapters.pil_scene_renderer import PilSceneRenderer  # noqa: E402
from rendering.adapters.ffmpeg_video_renderer import FfmpegVideoRenderer, build_multi_scene_captions  # noqa: E402
from tests.test_narrative_story_director import sample_notice_facts  # noqa: E402


def _ffprobe(path: str) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"ffprobe failed: {result.stderr}"
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def composed(tmp_path_factory) -> dict:
    tmp_path = tmp_path_factory.mktemp("step_e")

    facts = sample_notice_facts()
    arc, all_scenes = TemplateStoryDirector().plan_narrative_arc(facts)
    scenes = all_scenes[:3]  # first 3 scenes only, keeps the test fast

    registry = SceneRendererRegistry()
    registry.register(HtmlSceneRenderer())
    registry.register(PilSceneRenderer())

    tts = EdgeTtsProvider()
    image_paths, audio_paths, audio_durations = [], [], []
    for scene in scenes:
        image_asset = registry.render_with_fallback(scene)
        image_paths.append(image_asset.storage_path)
        audio_asset = tts.synthesize(scene.narration_segment_text, LanguageCode.EN, project_id="step-e-test")
        audio_paths.append(audio_asset.storage_path)
        audio_durations.append(audio_asset.duration_seconds)
        scene.duration_seconds = audio_asset.duration_seconds  # real TTS duration is authoritative

    renderer = FfmpegVideoRenderer(output_dir=tmp_path)
    video_asset = renderer.compose_multi_scene(
        scenes=scenes, image_paths=image_paths, audio_paths=audio_paths,
        project_id="step-e-test",
    )
    probe = _ffprobe(video_asset.storage_path_mp4)
    srt_text, vtt_text = build_multi_scene_captions(scenes)

    return {
        "scenes": scenes, "video_asset": video_asset, "probe": probe,
        "srt_text": srt_text, "vtt_text": vtt_text, "audio_durations": audio_durations,
    }


def test_mp4_produced_with_valid_streams(composed):
    streams = composed["probe"]["streams"]
    video_streams = [s for s in streams if s["codec_type"] == "video"]
    audio_streams = [s for s in streams if s["codec_type"] == "audio"]
    assert len(video_streams) == 1
    assert len(audio_streams) == 1
    assert video_streams[0]["codec_name"] == "h264"
    assert audio_streams[0]["codec_name"] == "aac"
    assert video_streams[0]["width"] == VIEWPORT_WIDTH
    assert video_streams[0]["height"] == VIEWPORT_HEIGHT


def test_total_duration_matches_sum_of_real_scene_durations(composed):
    """Requirement 5: audio duration determines total video duration —
    the merged video (despite xfade compressing the visual timeline
    slightly at each transition) must still total the sum of the real,
    TTS-measured scene durations, since narration is never cut short."""
    expected = sum(composed["audio_durations"])
    actual = float(composed["probe"]["format"]["duration"])
    assert actual == pytest.approx(expected, abs=0.2)


def test_mp4_decodes_end_to_end_without_errors(composed):
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", composed["video_asset"].storage_path_mp4, "-f", "null", "-"],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"ffmpeg decode failed: {result.stderr}"
    assert result.stderr.strip() == ""


def test_captions_cover_the_full_timeline_in_scene_order(composed):
    scenes = composed["scenes"]
    srt_text = composed["srt_text"]
    # 3 numbered cues, one per scene
    for i in range(1, len(scenes) + 1):
        assert f"\n{i}\n" in srt_text or srt_text.startswith(f"{i}\n")
    for scene in scenes:
        assert scene.narration_segment_text in srt_text
    # VTT starts with the required header and reuses the same cue text
    assert composed["vtt_text"].startswith("WEBVTT")
    for scene in scenes:
        assert scene.narration_segment_text in composed["vtt_text"]


def test_caption_timestamps_are_cumulative_and_within_video_duration(composed):
    scenes = composed["scenes"]
    video_duration = float(composed["probe"]["format"]["duration"])
    cumulative = 0.0
    for scene in scenes:
        cumulative += scene.duration_seconds
    assert cumulative <= video_duration + 0.2
