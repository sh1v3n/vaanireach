"""SceneRenderer — the Visual Strategy layer.

This is the layer that decouples "what representation was chosen"
(SceneDirector's job) from "which vendor/technique actually renders it"
(the provider layer). Each concrete SceneRenderer implements exactly one
SceneType (e.g. a MapSceneRenderer, an InfographicSceneRenderer, an
AiVideoSceneRenderer) and internally may call a VisualProvider,
VideoGenerationProvider, AudioProvider, or a local rendering tool — that
choice is entirely private to the renderer.

No concrete SceneRenderer exists yet in Phase 0. This file defines only
the strategy contract and a lookup registry so the orchestrator/media
agent can dispatch by SceneType without knowing implementation details:

    renderer = registry.get_renderer(scene.scene_type)
    asset = renderer.render_scene(scene)

Adding a new visual strategy (e.g. a THREE_D renderer) later never
requires changing SceneDirector, the orchestrator, verification, or the
dashboard — only registering a new SceneRenderer here.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from core.models.enums import SceneType
from core.models.media import MediaAsset
from core.models.storyboard import Scene


class SceneRenderer(ABC):
    @abstractmethod
    def supports(self, scene_type: SceneType) -> bool:
        raise NotImplementedError(
            "SceneRenderer.supports not implemented — Phase 0 interface stub"
        )

    @abstractmethod
    def render_scene(self, scene: Scene) -> MediaAsset:
        raise NotImplementedError(
            "SceneRenderer.render_scene not implemented — Phase 0 interface stub"
        )


class SceneRendererRegistry:
    """Maps SceneType -> SceneRenderer at runtime. Kept deliberately
    simple (a list + linear scan) since Phase 0 registers zero renderers —
    this is a dispatch contract, not an optimized lookup."""

    def __init__(self) -> None:
        self._renderers: list[SceneRenderer] = []

    def register(self, renderer: SceneRenderer) -> None:
        self._renderers.append(renderer)

    def get_renderer(self, scene_type: SceneType) -> SceneRenderer:
        for renderer in self._renderers:
            if renderer.supports(scene_type):
                return renderer
        raise LookupError(
            f"No SceneRenderer registered for scene_type={scene_type!r} — "
            "Phase 0 ships zero concrete renderers by design."
        )
