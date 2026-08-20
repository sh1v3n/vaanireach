"""Storyboard + Scene.

Kept in one file because they're always read/written together (1
storyboard -> N scenes). A Scene's `scene_type` is a SceneDirector decision
(see core/interfaces/scene_director.py) — it says WHAT representation to
use, never WHICH provider renders it.
"""
from __future__ import annotations

from pydantic import Field

from core.models.base import IdentifiedModel
from core.models.enums import LanguageCode, SceneType, StoryboardStatus


class Scene(IdentifiedModel):
    storyboard_id: str
    order_index: int
    scene_type: SceneType
    narration_segment_text: str
    claim_ids: list[str] = Field(default_factory=list)
    source_fact_ids: list[str] = Field(default_factory=list)
    duration_seconds: float
    visual_prompt: str | None = None
    media_asset_ids: list[str] = Field(default_factory=list)


class Storyboard(IdentifiedModel):
    project_id: str
    script_id: str
    language: LanguageCode
    scene_ids: list[str] = Field(default_factory=list)
    total_duration_seconds: float
    status: StoryboardStatus = StoryboardStatus.DRAFT
