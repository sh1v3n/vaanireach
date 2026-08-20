"""GroqTranslationProvider — TranslationProvider implementation backed by
Groq's OpenAI-compatible chat completions API (openai/gpt-oss-120b by
default). Chosen at the user's explicit request as the Gemini-free
translation path — Groq's free tier is fast and this model produced
clean, direct translations in testing (unlike a reasoning model tried
alongside it, which wrapped every answer in a large <think> block).

Unlike every other provider in this codebase, translation failures are
NOT caught and degraded to a local fallback — there is no meaningful
"local fallback" for "produce this narration in Hindi." Silently
returning English (or a placeholder) when a caller explicitly requested
Hindi would be a worse failure than a loud, unambiguous exception:
raising lets the caller decide (skip the language, retry, surface to a
human), rather than shipping mislabeled content.

Number/date/URL/phone-number preservation is a real design constraint
here, not a nicety: DeterministicFactVerifier's checks are digit/token
based, so a translation that converts "31 March 2026" into Devanagari
numerals or a URL into a transliterated form would make correctly
translated narration fail verification. Confirmed empirically (see
tests/test_groq_translation_provider.py) that instructing the model to
preserve these tokens verbatim works reliably.
"""
from __future__ import annotations

import os

import requests

from core.interfaces.translation_provider import TranslationProvider
from core.models.claim import Claim
from core.models.enums import LanguageCode
from core.models.storyboard import Scene

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-oss-120b"
REQUEST_TIMEOUT_SECONDS = 30.0

_LANGUAGE_NAMES: dict[LanguageCode, str] = {
    LanguageCode.EN: "English",
    LanguageCode.HI: "Hindi",
    LanguageCode.MR: "Marathi",
    LanguageCode.TA: "Tamil",
    LanguageCode.BN: "Bengali",
    LanguageCode.TE: "Telugu",
    LanguageCode.KN: "Kannada",
    LanguageCode.ML: "Malayalam",
    LanguageCode.GU: "Gujarati",
}

_PROMPT_TEMPLATE = (
    "Translate the following text from {source} to {target}. Preserve all numbers, "
    "dates, currency amounts, URLs, and phone numbers EXACTLY as written in the "
    "original (same digits, same script — do not convert to a different numeral "
    "system, do not spell them out, do not transliterate URLs). Output ONLY the "
    "translation, no explanation, no quotes, no extra text.\n\nText: \"{text}\""
)


class GroqTranslationRequestError(RuntimeError):
    """A translation call failed. Deliberately never caught internally —
    see module docstring for why this provider has no local fallback."""


class GroqTranslationProvider(TranslationProvider):
    def __init__(self, api_key: str | None = None, *, model: str | None = None, session: requests.Session | None = None) -> None:
        self.api_key = (api_key if api_key is not None else os.environ.get("GROQ_API_KEY", "")).strip()
        self.model = model or os.environ.get("GROQ_TRANSLATION_MODEL", DEFAULT_MODEL)
        self._session = session or requests.Session()

    # ---------------------------------------------------------------- TranslationProvider

    def supported_languages(self) -> list[LanguageCode]:
        return list(_LANGUAGE_NAMES.keys())

    def translate(self, text: str, source_language: LanguageCode, target_language: LanguageCode) -> str:
        if source_language == target_language:
            return text  # no network call needed for a same-language "translation"

        if not self.api_key:
            raise GroqTranslationRequestError("GROQ_API_KEY not configured")

        source_name = _LANGUAGE_NAMES.get(source_language, source_language.value)
        target_name = _LANGUAGE_NAMES.get(target_language, target_language.value)
        prompt = _PROMPT_TEMPLATE.format(source=source_name, target=target_name, text=text)

        try:
            response = self._session.post(
                GROQ_CHAT_URL,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.exceptions.RequestException as exc:
            raise GroqTranslationRequestError(f"network error calling Groq: {exc}") from exc

        if response.status_code != 200:
            raise GroqTranslationRequestError(f"Groq returned {response.status_code}: {response.text[:300]}")

        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise GroqTranslationRequestError(f"unexpected Groq response shape: {response.text[:300]}") from exc

        translated = content.strip().strip('"').strip()
        if not translated:
            raise GroqTranslationRequestError(f"Groq returned an empty translation for: {text!r}")
        return translated

    def translate_claims(self, claims: list[Claim], target_language: LanguageCode) -> list[Claim]:
        return [
            claim.model_copy(update={
                "id": Claim.model_fields["id"].default_factory(),  # a translated claim is a new artifact
                "claim_text": self.translate(claim.claim_text, claim.language, target_language),
                "language": target_language,
            })
            for claim in claims
        ]


def translate_scenes(scenes: list[Scene], provider: TranslationProvider, *, target_language: LanguageCode, source_language: LanguageCode = LanguageCode.EN) -> list[Scene]:
    """Translates each scene's narration_segment_text individually —
    never a single flat paragraph — so translation never touches
    structure, order, fact grounding, roles, or transitions. This is
    the language-independence design principle from
    docs/superpowers/specs/2026-08-20-narrative-story-director-design.md
    applied: NarrativeArc/StoryDirector run once; only narration text
    (and, downstream, the TTS duration derived from it) varies per
    language."""
    return [
        scene.model_copy(update={
            "narration_segment_text": provider.translate(scene.narration_segment_text, source_language, target_language),
        })
        for scene in scenes
    ]
