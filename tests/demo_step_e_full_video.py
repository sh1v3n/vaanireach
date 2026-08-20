"""Step E: the first COMPLETE VaaniReach video, end to end, English only.

Verified Fact Ledger -> TemplateStoryDirector -> NarrativeArc (8 scenes)
  -> SceneRendererRegistry (Html primary, Pillow fallback) per scene
  -> EdgeTtsProvider per scene (real measured duration is authoritative)
  -> FfmpegVideoRenderer.compose_multi_scene (Ken-Burns motion + real
     xfade transitions per TransitionType)
  -> claims_from_scenes + DeterministicFactVerifier (Final Verification)
  -> final MP4 + SRT + VTT + verification report

Run directly: PYTHONPATH=backend .venv/bin/python tests/demo_step_e_full_video.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.interfaces.scene_renderer import SceneRendererRegistry  # noqa: E402
from core.models.enums import LanguageCode, VerificationStatus  # noqa: E402
from providers.narrative.template_story_director import TemplateStoryDirector  # noqa: E402
from providers.tts.edge_tts_provider import EdgeTtsProvider  # noqa: E402
from providers.verification.deterministic_fact_verifier import DeterministicFactVerifier, claims_from_scenes  # noqa: E402
from rendering.adapters.html_scene_renderer import HtmlSceneRenderer, VIEWPORT_WIDTH, VIEWPORT_HEIGHT  # noqa: E402
from rendering.adapters.pil_scene_renderer import PilSceneRenderer  # noqa: E402
from rendering.adapters.ffmpeg_video_renderer import FfmpegVideoRenderer, build_multi_scene_captions  # noqa: E402
from tests.test_narrative_story_director import sample_notice_facts  # noqa: E402

PROJECT_ID = "step-e-full-video"


def main() -> None:
    t0 = time.time()

    # ---- 1/2: sample notice's verified Fact Ledger -> NarrativeArc ----
    facts = sample_notice_facts()
    director = TemplateStoryDirector()
    arc, scenes = director.plan_narrative_arc(facts)
    print(f"NarrativeArc: {len(scenes)} scenes, generator={arc.generator_name!r}")
    print(f"story_summary: {arc.story_summary}\n")

    # ---- 3: render every scene through the registry (Html primary, Pillow fallback) ----
    registry = SceneRendererRegistry()
    registry.register(HtmlSceneRenderer())
    registry.register(PilSceneRenderer())

    image_paths: list[str] = []
    renderer_used: list[str] = []
    for scene in scenes:
        asset = registry.render_with_fallback(scene)
        image_paths.append(asset.storage_path)
        renderer_used.append(asset.provider_name)
    print("Scene rendering:")
    for scene, provider in zip(scenes, renderer_used):
        print(f"  [{scene.narrative_role.value:12s}] scene_type={scene.scene_type.value:11s} via {provider}")

    # ---- 4/5: Edge-TTS per scene, real measured duration is authoritative ----
    tts = EdgeTtsProvider()
    audio_paths: list[str] = []
    voices_used: set[str] = set()
    for scene in scenes:
        audio_asset = tts.synthesize(scene.narration_segment_text, LanguageCode.EN, project_id=PROJECT_ID)
        audio_paths.append(audio_asset.storage_path)
        voices_used.add(audio_asset.voice_id)
        scene.duration_seconds = audio_asset.duration_seconds  # audio duration overrides the pre-TTS estimate
    print(f"\nTTS voice(s) used: {voices_used}")
    print("Real (post-TTS) scene durations:")
    for scene in scenes:
        print(f"  [{scene.narrative_role.value:12s}] {scene.duration_seconds:.3f}s  transition_out={scene.transition_to_next_scene}")

    # ---- 6/7: compose all scenes into ONE MP4 with Ken-Burns motion + real transitions ----
    print("\nComposing final video (this involves N zoompan renders + an xfade chain)...")
    video_renderer = FfmpegVideoRenderer()
    video_asset = video_renderer.compose_multi_scene(
        scenes=scenes, image_paths=image_paths, audio_paths=audio_paths, project_id=PROJECT_ID,
    )
    print(f"  video: {video_asset.storage_path_mp4}")

    # ---- 8: captions from actual narration + actual timings ----
    srt_text, vtt_text = build_multi_scene_captions(scenes)
    srt_path = Path(video_asset.storage_path_mp4).with_suffix(".srt")
    vtt_path = Path(video_asset.storage_path_mp4).with_suffix(".vtt")
    srt_path.write_text(srt_text, encoding="utf-8")
    vtt_path.write_text(vtt_text, encoding="utf-8")
    print(f"  srt:   {srt_path}")
    print(f"  vtt:   {vtt_path}")

    # ---- 10/11/12: Final Verification against the original Fact Ledger ----
    claims = claims_from_scenes(scenes, project_id=PROJECT_ID)
    verifier = DeterministicFactVerifier()
    results = verifier.verify_batch(claims, facts)
    n_verified = sum(1 for r in results if r.status == VerificationStatus.VERIFIED)
    n_blocking = sum(1 for r in results if r.is_blocking)
    print(f"\nFinal Verification: {n_verified}/{len(results)} claims VERIFIED, {n_blocking} blocking")
    for scene, claim, result in zip(scenes, claims, results):
        print(f"  [{scene.narrative_role.value:12s}] {result.status.value:12s} is_blocking={result.is_blocking}")
    video_valid = n_blocking == 0

    # ---- 14: ffprobe + decode verification ----
    probe = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
         video_asset.storage_path_mp4], capture_output=True, text=True, check=True,
    ).stdout)
    video_stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    audio_stream = next(s for s in probe["streams"] if s["codec_type"] == "audio")
    duration = float(probe["format"]["duration"])
    decode = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", video_asset.storage_path_mp4, "-f", "null", "-"],
        capture_output=True, text=True,
    )

    print("\n" + "=" * 70)
    print("FINAL REPORT")
    print("=" * 70)
    print(f"MP4 path:        {video_asset.storage_path_mp4}")
    print(f"Total duration:  {duration:.3f}s  (sum of real scene durations: {sum(s.duration_seconds for s in scenes):.3f}s)")
    print(f"Number of scenes: {len(scenes)}")
    print(f"Resolution:      {video_stream['width']}x{video_stream['height']}")
    print(f"Video codec:     {video_stream['codec_name']}")
    print(f"Audio codec:     {audio_stream['codec_name']} @ {audio_stream.get('sample_rate','?')}Hz")
    print(f"ffmpeg full-decode: exit={decode.returncode}  stderr={decode.stderr.strip() or '(none)'}")
    print(f"SRT path:        {srt_path}")
    print(f"VTT path:        {vtt_path}")
    print(f"Verification:    {'VALID' if video_valid else 'BLOCKED'} ({n_verified}/{len(results)} verified, {n_blocking} blocking)")
    print(f"\nScene-by-scene:")
    for scene in scenes:
        t = scene.transition_to_next_scene.value if scene.transition_to_next_scene else "(none)"
        print(f"  [{scene.order_index}] {scene.narrative_role.value:12s} {scene.duration_seconds:6.3f}s  -> {t}")
    print(f"\nElapsed: {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
