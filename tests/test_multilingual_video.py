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
from rendering.multilingual_video import generate_language_video, run_full_pipeline  # noqa: E402
from tests.test_narrative_story_director import sample_notice_facts  # noqa: E402

_HAS_KEYS = bool(os.environ.get("GROQ_API_KEY")) and bool(os.environ.get("SARVAM_API_KEYS"))


@pytest.mark.skipif(not _HAS_KEYS, reason="GROQ_API_KEY/SARVAM_API_KEYS not set")
def test_generates_a_hindi_video_reusing_the_same_images():
    """This test's own scope is translation/TTS (real GroqTranslationProvider
    + real SarvamTTSProvider) — it deliberately fakes the avatar/visual
    providers (dedicated avatar tests exist separately below) so a plain
    `pytest` run of this file never silently makes a real, paid Hedra/D-ID
    call. Confirmed the hard way: before this fix, running this file
    against a funded D-ID account consumed 2 real trial credits per run
    with no test asserting anything about the avatar at all."""
    facts = sample_notice_facts()
    director = TemplateStoryDirector()
    _, scenes = director.plan_narrative_arc(facts)

    renderer = PilSceneRenderer()
    image_paths = [renderer.render_scene(s).storage_path for s in scenes]

    result = generate_language_video(
        facts, image_paths,
        story_director=director, translator=GroqTranslationProvider(),
        target_language=LanguageCode.HI, project_id="multilingual-test",
        avatar_provider=_FakeAvatarProvider(), visual_provider=_FakeVisualProvider(),
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
    # captions/subtitles stay in English regardless of audio language —
    # only the spoken audio is Hindi, not what's displayed on screen
    assert not any("ऀ" <= ch <= "ॿ" for ch in result.srt_text), "captions must stay English even for Hindi audio"
    assert not any("ऀ" <= ch <= "ॿ" for ch in result.vtt_text), "captions must stay English even for Hindi audio"
    assert result.facts == facts
    assert result.image_paths == image_paths
    assert len(result.verification_results) == len(result.scenes)
    assert all(r.is_blocking is False for r in result.verification_results)


@pytest.mark.skipif(not _HAS_KEYS, reason="GROQ_API_KEY/SARVAM_API_KEYS not set")
def test_english_target_skips_translation_but_still_regenerates_video():
    """See test_generates_a_hindi_video_reusing_the_same_images's docstring
    — same reasoning: this test's scope is TTS/verification, so the avatar
    step is faked to avoid a real, paid vendor call on every test run."""
    facts = sample_notice_facts()
    director = TemplateStoryDirector()
    _, scenes = director.plan_narrative_arc(facts)
    renderer = PilSceneRenderer()
    image_paths = [renderer.render_scene(s).storage_path for s in scenes]

    result = generate_language_video(
        facts, image_paths,
        story_director=director, translator=GroqTranslationProvider(),
        target_language=LanguageCode.EN, project_id="multilingual-test-en",
        avatar_provider=_FakeAvatarProvider(), visual_provider=_FakeVisualProvider(),
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


class _FakeAvatarProvider:
    """generate_avatar_hook without ever calling Hedra/D-ID - returns a
    real short local MP4 (via the same Tier-3 placeholder generator
    AvatarFailoverProvider itself uses) so downstream ffmpeg compositing
    gets a genuinely playable file."""

    def __init__(self, *, should_fail: bool = False, tier: int = 1) -> None:
        self.should_fail = should_fail
        self.tier = tier  # 1=Hedra, 2=D-ID, 3=Tier-3 static placeholder — mirrors AvatarFailoverProvider
        self.calls: list[tuple[str, str]] = []  # (image_path, audio_path)

    def generate_avatar_hook(self, image_path, audio_path, *, project_id, scene_id=None, text_prompt="", aspect_ratio="9:16"):
        from providers.video.avatar_provider import ensure_fallback_asset
        from core.models.enums import GenerationStatus, MediaAssetType
        from core.models.media import MediaAsset

        self.calls.append((image_path, audio_path))
        if self.should_fail:
            raise RuntimeError("simulated avatar provider failure")
        path = ensure_fallback_asset()
        return MediaAsset(
            project_id=project_id, asset_type=MediaAssetType.VIDEO_CLIP, storage_path=path,
            provider_name="fake-avatar", generation_status=GenerationStatus.COMPLETE,
            metadata={"tier": self.tier},
        )


class _FakeVisualProvider:
    def generate_image(self, prompt, scene, *, project_id, width=None, height=None):
        from core.models.enums import GenerationStatus, MediaAssetType
        from core.models.media import MediaAsset
        return MediaAsset(
            project_id=project_id, scene_id=scene.id, asset_type=MediaAssetType.IMAGE,
            storage_path=_dummy_portrait_path(), provider_name="fake-visual",
            generation_status=GenerationStatus.COMPLETE,
        )


def _dummy_portrait_path() -> str:
    """A real, tiny JPEG - Hedra/D-ID would need a real image, but the
    fake avatar provider above never actually reads this file, so any
    valid image on disk is fine for these tests."""
    import tempfile
    from PIL import Image
    path = Path(tempfile.gettempdir()) / "test_avatar_portrait.jpg"
    if not path.exists():
        Image.new("RGB", (200, 280), (128, 128, 128)).save(path, format="JPEG")
    return str(path)


@pytest.mark.skipif(not _HAS_KEYS, reason="GROQ_API_KEY/SARVAM_API_KEYS not set")
def test_generate_language_video_composites_avatar_and_captions_by_default():
    """Global constraint: avatar+PiP+caption compositing is REQUIRED, not
    optional - a normal successful run must produce avatar_composited=True."""
    facts = sample_notice_facts()
    director = TemplateStoryDirector()
    _, scenes = director.plan_narrative_arc(facts)
    renderer = PilSceneRenderer()
    image_paths = [renderer.render_scene(s).storage_path for s in scenes]

    fake_avatar = _FakeAvatarProvider(tier=1)
    fake_visual = _FakeVisualProvider()

    result = generate_language_video(
        facts, image_paths,
        story_director=director, translator=GroqTranslationProvider(),
        target_language=LanguageCode.EN, project_id="test-avatar-wiring",
        avatar_provider=fake_avatar, visual_provider=fake_visual,
    )
    assert result.avatar_composited is True
    assert result.avatar_tier == 1
    assert len(fake_avatar.calls) == 1
    assert result.video_asset.storage_path_mp4 is not None
    assert Path(result.video_asset.storage_path_mp4).exists()


@pytest.mark.skipif(not _HAS_KEYS, reason="GROQ_API_KEY/SARVAM_API_KEYS not set")
def test_generate_language_video_reports_tier_3_placeholder_via_avatar_tier():
    """Finding #1 (final whole-branch review): a Tier-3 static placeholder
    (both real vendors exhausted) still composites successfully - the
    caller must be able to tell it apart from a real Tier 1/2 avatar via
    avatar_tier, not just avatar_composited."""
    facts = sample_notice_facts()
    director = TemplateStoryDirector()
    _, scenes = director.plan_narrative_arc(facts)
    renderer = PilSceneRenderer()
    image_paths = [renderer.render_scene(s).storage_path for s in scenes]

    fake_avatar = _FakeAvatarProvider(tier=3)
    fake_visual = _FakeVisualProvider()

    result = generate_language_video(
        facts, image_paths,
        story_director=director, translator=GroqTranslationProvider(),
        target_language=LanguageCode.EN, project_id="test-avatar-tier3",
        avatar_provider=fake_avatar, visual_provider=fake_visual,
    )
    assert result.avatar_composited is True
    assert result.avatar_tier == 3
    assert result.video_asset.storage_path_mp4 is not None
    assert Path(result.video_asset.storage_path_mp4).exists()


@pytest.mark.skipif(not _HAS_KEYS, reason="GROQ_API_KEY/SARVAM_API_KEYS not set")
def test_generate_language_video_falls_back_when_compositing_fails():
    """Reliability fallback (not a design option - see Global Constraints):
    when the avatar/compositing step raises, the run must still return a
    valid, playable video (the plain captioned B-roll), not crash."""
    facts = sample_notice_facts()
    director = TemplateStoryDirector()
    _, scenes = director.plan_narrative_arc(facts)
    renderer = PilSceneRenderer()
    image_paths = [renderer.render_scene(s).storage_path for s in scenes]

    fake_avatar = _FakeAvatarProvider(should_fail=True)
    fake_visual = _FakeVisualProvider()

    result = generate_language_video(
        facts, image_paths,
        story_director=director, translator=GroqTranslationProvider(),
        target_language=LanguageCode.EN, project_id="test-avatar-fallback",
        avatar_provider=fake_avatar, visual_provider=fake_visual,
    )
    assert result.avatar_composited is False
    assert result.avatar_tier is None
    assert result.video_asset.storage_path_mp4 is not None
    assert Path(result.video_asset.storage_path_mp4).exists()


@pytest.mark.skipif(not _HAS_KEYS, reason="GROQ_API_KEY/SARVAM_API_KEYS not set")
def test_run_full_pipeline_extracts_real_facts_from_raw_text_and_produces_a_video():
    """The real "raw document text in, video out" path — extraction and
    fact-aware image prompting are real (the two new capabilities this
    test exists to cover); avatar/visual image generation are faked to
    keep this test free/fast (both already have their own dedicated
    coverage) without touching real D-ID credits or Cloudflare quota."""
    document_text = (
        "Kisan Sahayata Yojana (Farmer Support Scheme)\n"
        "Office of the District Collectorate, Riverbend District\n\n"
        "The District Collectorate is pleased to announce the Kisan Sahayata Yojana. "
        "Eligible recipients will receive Rs 2,000. This scheme is open to farmers holding "
        "less than 2 hectares of land in Riverbend District. To apply, visit "
        "www.example-scheme.gov.in or register at your nearest Common Service Centre. "
        "Applications close on 31 March 2026."
    )
    fake_avatar = _FakeAvatarProvider()
    fake_visual = _FakeVisualProvider()

    results = run_full_pipeline(
        document_text, languages=[LanguageCode.EN], project_id="test-full-pipeline",
        avatar_provider=fake_avatar, visual_provider=fake_visual,
    )

    assert len(results) == 1
    result = results[0]
    assert result.language == LanguageCode.EN
    assert len(result.scenes) > 0
    # real extraction found real facts, not an empty/placeholder ledger
    assert result.verified_count > 0
    assert result.video_asset.storage_path_mp4 is not None
    assert Path(result.video_asset.storage_path_mp4).exists()


def test_run_full_pipeline_raises_clearly_when_no_facts_are_extracted():
    class _EmptyExtractor:
        def extract_facts(self, document_id, pages, *, project_id):
            return []

    with pytest.raises(ValueError, match="zero facts"):
        run_full_pipeline(
            "irrelevant text", languages=[LanguageCode.EN], project_id="test-empty",
            llm_provider=_EmptyExtractor(),
        )
