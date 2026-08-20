"""TTSProvider — abstraction over whichever text-to-speech vendor is
eventually selected (see ADR-006). No vendor is referenced here."""
from __future__ import annotations

from abc import ABC, abstractmethod

from core.models.enums import GenerationStatus, LanguageCode
from core.models.media import AudioAsset


class TTSProvider(ABC):
    @abstractmethod
    def list_voices(self, language: LanguageCode) -> list[str]:
        raise NotImplementedError("TTSProvider.list_voices not implemented — Phase 0 interface stub")

    @abstractmethod
    def synthesize(self, text: str, language: LanguageCode, voice_id: str | None = None) -> AudioAsset:
        raise NotImplementedError("TTSProvider.synthesize not implemented — Phase 0 interface stub")

    @abstractmethod
    def get_status(self, job_id: str) -> GenerationStatus:
        raise NotImplementedError("TTSProvider.get_status not implemented — Phase 0 interface stub")
