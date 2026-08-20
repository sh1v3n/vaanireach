"""TranslationProvider — abstraction over whichever translation
service/model is eventually selected (see ADR-006). Not hardcoded to any
vendor and not hardcoded to exactly 3 languages."""
from __future__ import annotations

from abc import ABC, abstractmethod

from core.models.claim import Claim
from core.models.enums import LanguageCode


class TranslationProvider(ABC):
    @abstractmethod
    def supported_languages(self) -> list[LanguageCode]:
        raise NotImplementedError(
            "TranslationProvider.supported_languages not implemented — Phase 0 interface stub"
        )

    @abstractmethod
    def translate(self, text: str, source_language: LanguageCode, target_language: LanguageCode) -> str:
        raise NotImplementedError(
            "TranslationProvider.translate not implemented — Phase 0 interface stub"
        )

    @abstractmethod
    def translate_claims(self, claims: list[Claim], target_language: LanguageCode) -> list[Claim]:
        raise NotImplementedError(
            "TranslationProvider.translate_claims not implemented — Phase 0 interface stub"
        )
