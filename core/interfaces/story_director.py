"""StoryDirector — the narrative layer above SceneDirector. Decides HOW
verified facts should be presented as a coherent story (context ->
announcement -> benefit -> eligibility -> action -> urgency -> CTA)
instead of one scene per fact. Pure decision logic over already-verified
facts: it never invents a number, date, name, location, organization,
scheme detail, eligibility requirement, deadline, URL, phone number, or
claim that isn't already in the Source Fact Ledger it was given.

SceneDirector's job is unchanged and still runs after this: StoryDirector
decides the story shape and narrative_role per scene; SceneDirector still
decides scene_type (the visual representation) per scene.

See docs/superpowers/specs/2026-08-20-narrative-story-director-design.md.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from core.models.fact import SourceFact
from core.models.narrative import NarrativeArc
from core.models.storyboard import Scene


class StoryDirector(ABC):
    @abstractmethod
    def plan_narrative_arc(self, facts: list[SourceFact]) -> tuple[NarrativeArc, list[Scene]]:
        """Returns both the arc and its scenes (not ids to resolve later)
        since this codebase has no persistence/repository layer yet.
        `arc.scene_ids` must equal `[s.id for s in scenes]`, in order."""
        raise NotImplementedError(
            "StoryDirector.plan_narrative_arc not implemented — interface stub"
        )
