"""VisualConcept, NarrativeArc — the story layer that sits above
SceneDirector (see core/interfaces/story_director.py). A NarrativeArc
groups verified facts into a coherent story (HOOK -> CONTEXT ->
ANNOUNCEMENT -> ... -> CTA -> CLOSING) instead of one scene per fact.

Structure here — roles, order, fact assignment, visual concepts,
transitions — is language-independent: it's decided once from the
(language-independent) Source Fact Ledger. Per-language narration is
produced by translating each scene's narration_segment_text afterward,
not by re-running the StoryDirector. See
docs/superpowers/specs/2026-08-20-narrative-story-director-design.md.
"""
from __future__ import annotations

from core.models.base import IdentifiedModel, VaaniBaseModel
from core.models.enums import StoryboardStatus


class VisualConcept(VaaniBaseModel):
    """What a scene's visual should DO, not what it should say —
    narration explains the fact, the visual contextualizes/demonstrates/
    reinforces it. `elements` is the ordered chain a renderer composes,
    e.g. ["farmer_icon", "govt_building_icon", "rupee_badge"] for
    "farmer -> government assistance -> benefit". `visual_beats`
    describes what happens/changes on screen, in order — action
    descriptions like "Animate Rs.2,000 benefit toward farmer", never a
    restatement of the narration text."""

    summary: str
    elements: list[str]
    visual_beats: list[str]


class NarrativeArc(IdentifiedModel):
    project_id: str
    document_id: str
    title: str
    story_summary: str
    target_duration_seconds: float
    scene_ids: list[str]
    """Ordered. Must match [s.id for s in scenes] from whatever produced
    this arc — see StoryDirector.plan_narrative_arc, which returns both."""
    generator_name: str
    status: StoryboardStatus = StoryboardStatus.DRAFT
