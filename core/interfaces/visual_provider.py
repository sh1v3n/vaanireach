"""VisualProvider — abstraction over a still-image / graphic generation
vendor. Undecided (see ADR-004). Consumed by a SceneRenderer, never called
directly by the orchestrator or agents."""
from __future__ import annotations

from abc import ABC, abstractmethod

from core.models.enums import GenerationStatus
from core.models.media import MediaAsset
from core.models.storyboard import Scene


class VisualProvider(ABC):
    @abstractmethod
    def generate_image(self, prompt: str, scene: Scene) -> MediaAsset:
        raise NotImplementedError(
            "VisualProvider.generate_image not implemented — Phase 0 interface stub"
        )

    @abstractmethod
    def get_status(self, job_id: str) -> GenerationStatus:
        raise NotImplementedError(
            "VisualProvider.get_status not implemented — Phase 0 interface stub"
        )

    @abstractmethod
    def cancel(self, job_id: str) -> None:
        raise NotImplementedError(
            "VisualProvider.cancel not implemented — Phase 0 interface stub"
        )
