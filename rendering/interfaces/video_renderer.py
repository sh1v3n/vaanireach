"""VideoRenderer — final composition abstraction: scenes + audio +
captions + visual assets + transitions + timing + branding -> MP4/SRT/VTT.
Implementation (FFmpeg-based, Remotion, MoviePy, or something else) is
deliberately undecided — see ADR-005. rendering/adapters/ is where a
concrete implementation would eventually live."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from core.models.enums import GenerationStatus
from core.models.media import AudioAsset, MediaAsset, VideoAsset
from core.models.script import Script
from core.models.storyboard import Scene
from core.models.translation import Translation


class VideoRenderer(ABC):
    @abstractmethod
    def render(
        self,
        scenes: list[Scene],
        audio_assets: list[AudioAsset],
        captions: str | None,
        visual_assets: list[MediaAsset],
        transitions: list[str],
        branding: dict[str, Any] | None,
    ) -> VideoAsset:
        raise NotImplementedError("VideoRenderer.render not implemented — Phase 0 interface stub")

    @abstractmethod
    def export_captions(self, script: Script, translation: Translation | None, format: str) -> str:
        """`format` is e.g. "srt" or "vtt"; returns the caption file content."""
        raise NotImplementedError(
            "VideoRenderer.export_captions not implemented — Phase 0 interface stub"
        )

    @abstractmethod
    def get_status(self, job_id: str) -> GenerationStatus:
        raise NotImplementedError(
            "VideoRenderer.get_status not implemented — Phase 0 interface stub"
        )
