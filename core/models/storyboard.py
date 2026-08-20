"""Storyboard + Scene.

Kept in one file because they're always read/written together (1
storyboard -> N scenes). A Scene's `scene_type` is a SceneDirector decision
(see core/interfaces/scene_director.py) — it says WHAT representation to
use, never WHICH provider renders it.
"""
from __future__ import annotations

from pydantic import Field

from core.models.base import IdentifiedModel
from core.models.enums import LanguageCode, NarrativeRole, SceneType, StoryboardStatus, TransitionType
from core.models.narrative import VisualConcept


class Scene(IdentifiedModel):
    storyboard_id: str
    order_index: int
    scene_type: SceneType
    narrative_role: NarrativeRole
    """The story job this scene does (HOOK, BENEFIT, CTA, ...) — decided
    by the StoryDirector, independent of scene_type (the visual
    representation, decided by SceneDirector). See
    core/interfaces/story_director.py."""
    narration_segment_text: str
    claim_ids: list[str] = Field(default_factory=list)
    source_fact_ids: list[str] = Field(default_factory=list)
    duration_seconds: float
    visual_prompt: str | None = None
    visual_concept: VisualConcept | None = None
    """Structured visual composition guidance for the local HTML/CSS
    renderer (narrative layer) — distinct from `visual_prompt`, a plain
    free-text prompt string an AI image/video provider consumes
    (providers/video/avatar_provider.py, dashboard/app.py). Both can be
    set; they serve different renderers."""
    transition_to_next_scene: TransitionType | None = None
    """None on the last scene in a storyboard/arc."""
    media_asset_ids: list[str] = Field(default_factory=list)


class Storyboard(IdentifiedModel):
    project_id: str
    script_id: str
    language: LanguageCode
    scene_ids: list[str] = Field(default_factory=list)
    total_duration_seconds: float
    status: StoryboardStatus = StoryboardStatus.DRAFT
