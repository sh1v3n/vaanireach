"""AudioProvider — abstraction over background-audio/music generation
(distinct from TTSProvider, which handles narration). Undecided (see
ADR-004/006)."""
from __future__ import annotations

from abc import ABC, abstractmethod

from core.models.enums import GenerationStatus
from core.models.media import AudioAsset


class AudioProvider(ABC):
    @abstractmethod
    def generate_background_audio(self, mood: str, duration_seconds: float) -> AudioAsset:
        raise NotImplementedError(
            "AudioProvider.generate_background_audio not implemented — Phase 0 interface stub"
        )

    @abstractmethod
    def get_status(self, job_id: str) -> GenerationStatus:
        raise NotImplementedError(
            "AudioProvider.get_status not implemented — Phase 0 interface stub"
        )

    @abstractmethod
    def cancel(self, job_id: str) -> None:
        raise NotImplementedError(
            "AudioProvider.cancel not implemented — Phase 0 interface stub"
        )
