"""generate_language_video — the "select a language" entry point.
Uses real Groq translation + real Sarvam TTS + real ffmpeg composition;
requires GROQ_API_KEY and SARVAM_API_KEYS, skipped otherwise. Uses
already-rendered local (Pillow) images rather than Cloudflare, to keep
this test fast and free — the image source is orthogonal to what this
module actually varies (translation/audio/timing per language).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from core.models.enums import LanguageCode  # noqa: E402
from providers.narrative.template_story_director import TemplateStoryDirector  # noqa: E402
from providers.translation.groq_translation_provider import GroqTranslationProvider  # noqa: E402
from rendering.adapters.pil_scene_renderer import PilSceneRenderer  # noqa: E402
from rendering.multilingual_video import generate_language_video  # noqa: E402
from tests.test_narrative_story_director import sample_notice_facts  # noqa: E402

_HAS_KEYS = bool(os.environ.get("GROQ_API_KEY")) and bool(os.environ.get("SARVAM_API_KEYS"))


@pytest.mark.skipif(not _HAS_KEYS, reason="GROQ_API_KEY/SARVAM_API_KEYS not set")
def test_generates_a_hindi_video_reusing_the_same_images():
    facts = sample_notice_facts()
    director = TemplateStoryDirector()
    _, scenes = director.plan_narrative_arc(facts)

    renderer = PilSceneRenderer()
    image_paths = [renderer.render_scene(s).storage_path for s in scenes]

    result = generate_language_video(
        facts, image_paths,
        story_director=director, translator=GroqTranslationProvider(),
        target_language=LanguageCode.HI, project_id="multilingual-test",
    )

    assert result.language == LanguageCode.HI
    assert result.video_asset.storage_path_mp4 is not None
    assert Path(result.video_asset.storage_path_mp4).exists()
    assert result.blocking_count == 0, "translated narration failed fact verification"
    assert result.verified_count == len(result.scenes)
    # narration is genuinely translated, not English passed through
    assert any("ऀ" <= ch <= "ॿ" for s in result.scenes for ch in s.narration_segment_text)
    assert result.srt_text.strip() != ""
    assert result.vtt_text.startswith("WEBVTT")


@pytest.mark.skipif(not _HAS_KEYS, reason="GROQ_API_KEY/SARVAM_API_KEYS not set")
def test_english_target_skips_translation_but_still_regenerates_video():
    facts = sample_notice_facts()
    director = TemplateStoryDirector()
    _, scenes = director.plan_narrative_arc(facts)
    renderer = PilSceneRenderer()
    image_paths = [renderer.render_scene(s).storage_path for s in scenes]

    result = generate_language_video(
        facts, image_paths,
        story_director=director, translator=GroqTranslationProvider(),
        target_language=LanguageCode.EN, project_id="multilingual-test-en",
    )
    assert result.language == LanguageCode.EN
    assert result.blocking_count == 0
    for scene, original in zip(result.scenes, scenes):
        assert scene.narration_segment_text == original.narration_segment_text


def test_mismatched_image_count_raises_clearly():
    facts = sample_notice_facts()
    director = TemplateStoryDirector()

    class _StubTranslator:
        def translate(self, *a, **kw):
            raise AssertionError("should not be called")

    with pytest.raises(ValueError, match="image_paths"):
        generate_language_video(
            facts, ["only_one_image.png"],
            story_director=director, translator=_StubTranslator(),
            target_language=LanguageCode.HI, project_id="x",
        )
