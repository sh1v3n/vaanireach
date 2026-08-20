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

import logging
from abc import ABC, abstractmethod

from core.models.enums import SceneType
from core.models.media import MediaAsset
from core.models.storyboard import Scene

logger = logging.getLogger("vaanireach.core.scene_renderer")


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

    def render_with_fallback(self, scene: Scene) -> MediaAsset:
        """Tries every renderer registered for scene.scene_type, in
        registration order, returning the first successful MediaAsset.
        A renderer's exception is logged and the next candidate is tried —
        this is what makes a lower-priority registration (e.g.
        PilSceneRenderer after HtmlSceneRenderer) an automatic fallback
        rather than something a caller has to implement itself."""
        candidates = [r for r in self._renderers if r.supports(scene.scene_type)]
        if not candidates:
            raise LookupError(
                f"No SceneRenderer registered for scene_type={scene.scene_type!r}"
            )
        last_exc: Exception | None = None
        for renderer in candidates:
            try:
                return renderer.render_scene(scene)
            except Exception as exc:  # noqa: BLE001 - any renderer failure falls through to the next candidate
                logger.warning(
                    "render_with_fallback: %s failed for scene_type=%r (%s) — trying next renderer",
                    type(renderer).__name__, scene.scene_type, exc,
                )
                last_exc = exc
        raise RuntimeError(
            f"All {len(candidates)} renderer(s) failed for scene_type={scene.scene_type!r}"
        ) from last_exc
