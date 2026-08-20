"""MediaAsset, AudioAsset, VideoAsset — outputs of the (currently
unselected) visual/audio/video provider layer. `provider_name` /
`tts_provider` / `renderer_name` are free-text so nothing here hardcodes a
vendor — see docs/decisions/ADR-004/005/006.md."""
from __future__ import annotations

from typing import Any

from pydantic import Field

from core.models.base import IdentifiedModel
from core.models.enums import GenerationStatus, LanguageCode, MediaAssetType


class MediaAsset(IdentifiedModel):
    project_id: str
    scene_id: str | None = None
    asset_type: MediaAssetType
    storage_path: str | None = None
    provider_name: str | None = None
    generation_status: GenerationStatus = GenerationStatus.PENDING
    prompt_used: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AudioAsset(IdentifiedModel):
    project_id: str
    scene_id: str | None = None
    script_id: str | None = None
    language: LanguageCode
    storage_path: str | None = None
    duration_seconds: float | None = None
    voice_id: str | None = None
    tts_provider: str | None = None
    generation_status: GenerationStatus = GenerationStatus.PENDING


class VideoAsset(IdentifiedModel):
    project_id: str
    storyboard_id: str
    language: LanguageCode
    storage_path_mp4: str | None = None
    storage_path_srt: str | None = None
    storage_path_vtt: str | None = None
    duration_seconds: float | None = None
    renderer_name: str | None = None
    generation_status: GenerationStatus = GenerationStatus.PENDING
