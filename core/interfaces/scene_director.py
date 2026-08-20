"""SceneDirector — decides WHAT visual representation fits a fact/claim
(e.g. an amount -> animated number, a location -> map, a deadline -> a
calendar animation, a statistic -> a chart). Pure decision logic: it
returns a SceneType, never a rendered asset. Rendering is a separate
concern — see core/interfaces/scene_renderer.py.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from core.models.claim import Claim
from core.models.enums import SceneType
from core.models.fact import SourceFact
from core.models.script import Script
from core.models.storyboard import Storyboard


class SceneDirector(ABC):
    @abstractmethod
    def choose_scene_type(self, fact: SourceFact, claim: Claim) -> SceneType:
        raise NotImplementedError(
            "SceneDirector.choose_scene_type not implemented — Phase 0 interface stub"
        )

    @abstractmethod
    def plan_storyboard(self, script: Script) -> Storyboard:
        raise NotImplementedError(
            "SceneDirector.plan_storyboard not implemented — Phase 0 interface stub"
        )
