"""Same as demo_cloudflare_contextual_video.py, but narration comes from
SarvamTTSProvider (real Sarvam voices, the user's own keys) instead of
plain EdgeTtsProvider — Sarvam's own vertical failover still falls back
to edge-tts internally on failure, which is exactly the mismatched-
audio-format scenario (24kHz mono vs 44.1kHz stereo) the
_concat_audio fix (tests/test_ffmpeg_audio_concat_mismatched_formats.py)
was built for, so this script also reports which vendor served each
scene.

Run: PYTHONPATH=backend .venv/bin/python tests/demo_sarvam_contextual_video.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from core.models.enums import LanguageCode  # noqa: E402
from providers.narrative.template_story_director import TemplateStoryDirector  # noqa: E402
from providers.tts.sarvam_tts_provider import SarvamTTSProvider  # noqa: E402
from providers.visual.cloudflare_provider import CloudflareVisualProvider  # noqa: E402
from rendering.adapters.cloudflare_scene_renderer import CloudflareSceneRenderer, build_scene_image_prompt  # noqa: E402
from rendering.adapters.ffmpeg_video_renderer import FfmpegVideoRenderer, build_multi_scene_captions  # noqa: E402
from tests.test_narrative_story_director import sample_notice_facts  # noqa: E402

PROJECT_ID = "sarvam-contextual-demo"


def main() -> None:
    facts = sample_notice_facts()
    arc, scenes = TemplateStoryDirector().plan_narrative_arc(facts)
    print(f"NarrativeArc: {len(scenes)} scenes\n")

    visual_provider = CloudflareVisualProvider()
    renderer = CloudflareSceneRenderer(visual_provider=visual_provider, project_id=PROJECT_ID)
    tts = SarvamTTSProvider()
    print(f"Sarvam manager configured: {tts.manager is not None}\n")

    image_paths, audio_paths, tts_providers = [], [], []
    for scene in scenes:
        prompt = build_scene_image_prompt(scene)
        print(f"[{scene.narrative_role.value:12s}]")
        image_asset = renderer.render_scene(scene)
        print(f"  image: {image_asset.provider_name}")
        image_paths.append(image_asset.storage_path)

        audio_asset = tts.synthesize(scene.narration_segment_text, LanguageCode.EN, project_id=PROJECT_ID)
        scene.duration_seconds = audio_asset.duration_seconds
        audio_paths.append(audio_asset.storage_path)
        tts_providers.append(audio_asset.tts_provider)
        print(f"  audio: {audio_asset.tts_provider}  ({audio_asset.duration_seconds:.2f}s)\n")

    print("Composing final video (Ken-Burns + real transitions, Step E engine)...")
    video_asset = FfmpegVideoRenderer().compose_multi_scene(
        scenes=scenes, image_paths=image_paths, audio_paths=audio_paths, project_id=PROJECT_ID,
    )
    srt_text, vtt_text = build_multi_scene_captions(scenes)
    srt_path = Path(video_asset.storage_path_mp4).with_suffix(".srt")
    srt_path.write_text(srt_text, encoding="utf-8")

    probe = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
         video_asset.storage_path_mp4], capture_output=True, text=True, check=True,
    ).stdout)
    decode = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", video_asset.storage_path_mp4, "-f", "null", "-"],
        capture_output=True, text=True,
    )

    print("\n=== DONE ===")
    print(f"MP4: {video_asset.storage_path_mp4}")
    print(f"SRT: {srt_path}")
    print(f"duration: {probe['format']['duration']}s (sum of real scene durations: {sum(s.duration_seconds for s in scenes):.2f}s)")
    print(f"decode check: exit={decode.returncode} stderr={decode.stderr.strip() or '(none)'}")
    print(f"TTS vendors used: {set(tts_providers)}")


if __name__ == "__main__":
    main()
