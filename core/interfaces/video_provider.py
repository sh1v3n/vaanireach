"""VideoGenerationProvider — abstraction over an AI video/avatar generation
vendor (e.g. a future Hedra/Runway/LTX-style provider — none selected, see
ADR-004). This is the interface a future provider adapter implements; the
rest of the system (document processing, translation, script generation,
verification, dashboard, orchestration) never imports a concrete provider,
only this interface."""
from __future__ import annotations

from abc import ABC, abstractmethod

from core.models.enums import GenerationStatus
from core.models.media import AudioAsset, MediaAsset, VideoAsset
from core.models.storyboard import Scene, Storyboard


class VideoGenerationProvider(ABC):
    @abstractmethod
    def generate_scene(self, scene: Scene) -> MediaAsset:
        raise NotImplementedError(
            "VideoGenerationProvider.generate_scene not implemented — Phase 0 interface stub"
        )

    @abstractmethod
    def generate_video(self, storyboard: Storyboard, audio_assets: list[AudioAsset]) -> VideoAsset:
        raise NotImplementedError(
            "VideoGenerationProvider.generate_video not implemented — Phase 0 interface stub"
        )

    @abstractmethod
    def get_status(self, job_id: str) -> GenerationStatus:
        raise NotImplementedError(
            "VideoGenerationProvider.get_status not implemented — Phase 0 interface stub"
        )

    @abstractmethod
    def cancel(self, job_id: str) -> None:
        raise NotImplementedError(
            "VideoGenerationProvider.cancel not implemented — Phase 0 interface stub"
        )
