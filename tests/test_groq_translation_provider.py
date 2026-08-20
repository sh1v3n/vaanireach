"""GroqTranslationProvider — real Groq call for translation quality/
number-preservation, plus per-scene translation orchestration and the
DeterministicFactVerifier compatibility this whole feature depends on
(translated narration must still pass verification against the
English-language Source Fact Ledger). Requires GROQ_API_KEY; skipped
otherwise.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from core.interfaces.translation_provider import TranslationProvider  # noqa: E402
from core.models.enums import LanguageCode  # noqa: E402
from providers.narrative.template_story_director import TemplateStoryDirector  # noqa: E402
from providers.translation.groq_translation_provider import (  # noqa: E402
    GroqTranslationProvider, translate_scenes,
)
from providers.verification.deterministic_fact_verifier import DeterministicFactVerifier, claims_from_scenes  # noqa: E402
from tests.test_narrative_story_director import sample_notice_facts  # noqa: E402

_HAS_KEY = bool(os.environ.get("GROQ_API_KEY"))


@pytest.mark.skipif(not _HAS_KEY, reason="GROQ_API_KEY not set")
def test_translates_english_to_hindi():
    provider = GroqTranslationProvider()
    result = provider.translate("This scheme is open to farmers.", LanguageCode.EN, LanguageCode.HI)
    assert result.strip() != ""
    assert result != "This scheme is open to farmers."
    # rough sanity check: contains Devanagari script
    assert any("ऀ" <= ch <= "ॿ" for ch in result)


def test_identity_translation_is_a_noop():
    """EN -> EN should never make a network call — same-language "translation" is trivial."""
    provider = GroqTranslationProvider(api_key="unused-should-never-be-called")
    result = provider.translate("Hello there.", LanguageCode.EN, LanguageCode.EN)
    assert result == "Hello there."


def test_bad_api_key_raises_rather_than_silently_returning_english():
    """Translation failures must be loud, not silently substitute the
    wrong language — unlike image/TTS providers, there's no acceptable
    local fallback for 'produce this in Hindi'."""
    provider = GroqTranslationProvider(api_key="invalid-key-12345")
    with pytest.raises(Exception):
        provider.translate("Hello there.", LanguageCode.EN, LanguageCode.HI)


@pytest.mark.skipif(not _HAS_KEY, reason="GROQ_API_KEY not set")
def test_numbers_dates_urls_phone_numbers_preserved_verbatim():
    provider = GroqTranslationProvider()
    text = "Applications close on 31 March 2026 at www.example-scheme.gov.in. Call 1800-000-0000. Receive ₹2,000."
    result = provider.translate(text, LanguageCode.EN, LanguageCode.HI)
    for token in ["31", "2026", "www.example-scheme.gov.in", "1800-000-0000", "2,000"]:
        assert token in result, f"{token!r} not preserved verbatim in translation: {result!r}"


@pytest.mark.skipif(not _HAS_KEY, reason="GROQ_API_KEY not set")
def test_translate_scenes_preserves_everything_but_narration():
    facts = sample_notice_facts()
    _, scenes = TemplateStoryDirector().plan_narrative_arc(facts)
    provider = GroqTranslationProvider()

    translated = translate_scenes(scenes, provider, target_language=LanguageCode.HI)

    assert len(translated) == len(scenes)
    for original, t in zip(scenes, translated):
        assert t.id == original.id  # same scene identity
        assert t.narrative_role == original.narrative_role
        assert t.source_fact_ids == original.source_fact_ids
        assert t.transition_to_next_scene == original.transition_to_next_scene
        assert t.narration_segment_text != original.narration_segment_text  # actually translated
        assert any("ऀ" <= ch <= "ॿ" for ch in t.narration_segment_text)


@pytest.mark.skipif(not _HAS_KEY, reason="GROQ_API_KEY not set")
def test_translated_narration_passes_the_same_fact_verifier():
    """The whole point of preserving numbers verbatim: translated
    narration must still pass DeterministicFactVerifier's digit/date/
    URL/phone checks against the original (English-language) Fact Ledger."""
    facts = sample_notice_facts()
    _, scenes = TemplateStoryDirector().plan_narrative_arc(facts)
    provider = GroqTranslationProvider()
    translated = translate_scenes(scenes, provider, target_language=LanguageCode.HI)

    claims = claims_from_scenes(translated, language=LanguageCode.HI)
    results = DeterministicFactVerifier().verify_batch(claims, facts)
    for scene, claim, result in zip(translated, claims, results):
        from core.models.enums import VerificationStatus
        assert result.status == VerificationStatus.VERIFIED, (
            f"translated scene {scene.narrative_role} failed verification: {result.explanation}\n"
            f"narration: {scene.narration_segment_text!r}"
        )
