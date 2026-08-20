"""The full "select a language" deliverable: real Cloudflare contextual
images (rendered ONCE, language-independent) + real Groq translation +
real Sarvam TTS, producing a video per selected language — same visuals,
correct per-language audio/timing.

Run: PYTHONPATH=backend .venv/bin/python tests/demo_multilingual_video.py [LANG ...]
  e.g. PYTHONPATH=backend .venv/bin/python tests/demo_multilingual_video.py hi mr
  (defaults to en hi mr if no languages given)
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
from providers.translation.groq_translation_provider import GroqTranslationProvider  # noqa: E402
from providers.visual.cloudflare_provider import CloudflareVisualProvider  # noqa: E402
from rendering.adapters.cloudflare_scene_renderer import CloudflareSceneRenderer, build_scene_image_prompt  # noqa: E402
from rendering.multilingual_video import generate_language_video  # noqa: E402
from tests.test_narrative_story_director import sample_notice_facts  # noqa: E402

PROJECT_ID = "multilingual-demo"


def main(language_codes: list[str]) -> None:
    languages = [LanguageCode(code) for code in language_codes]
    facts = sample_notice_facts()
    director = TemplateStoryDirector()
    _, scenes = director.plan_narrative_arc(facts)
    print(f"NarrativeArc: {len(scenes)} scenes (language-independent structure)\n")

    print("Rendering scene images ONCE (reused across every language)...")
    visual_provider = CloudflareVisualProvider()
    image_renderer = CloudflareSceneRenderer(visual_provider=visual_provider, project_id=PROJECT_ID)
    image_paths = []
    for scene in scenes:
        prompt = build_scene_image_prompt(scene)
        asset = image_renderer.render_scene(scene)
        image_paths.append(asset.storage_path)
        print(f"  [{scene.narrative_role.value:12s}] {asset.provider_name}")
    print()

    translator = GroqTranslationProvider()
    results = []
    for lang in languages:
        print(f"=== {lang.value} ===")
        result = generate_language_video(
            facts, image_paths,
            story_director=director, translator=translator,
            target_language=lang, project_id=PROJECT_ID,
        )
        results.append(result)

        probe = json.loads(subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
             result.video_asset.storage_path_mp4], capture_output=True, text=True, check=True,
        ).stdout)
        decode = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", result.video_asset.storage_path_mp4, "-f", "null", "-"],
            capture_output=True, text=True,
        )
        srt_path = Path(result.video_asset.storage_path_mp4).with_suffix(".srt")
        srt_path.write_text(result.srt_text, encoding="utf-8")

        print(f"  MP4: {result.video_asset.storage_path_mp4}")
        print(f"  SRT: {srt_path}")
        print(f"  duration: {probe['format']['duration']}s")
        print(f"  decode check: exit={decode.returncode} stderr={decode.stderr.strip() or '(none)'}")
        print(f"  verification: {result.verified_count}/{len(result.scenes)} verified, {result.blocking_count} blocking")
        print(f"  first scene narration: {result.scenes[0].narration_segment_text}")
        print()

    print("=== SUMMARY ===")
    for r in results:
        print(f"  {r.language.value}: {r.video_asset.storage_path_mp4}  ({r.verified_count}/{len(r.scenes)} verified)")


if __name__ == "__main__":
    langs = sys.argv[1:] or ["en", "hi", "mr"]
    main(langs)
