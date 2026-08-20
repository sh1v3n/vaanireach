# Media Agent

Dispatches each Scene to the right `SceneRenderer` (by `SceneType`) via
`SceneRendererRegistry`, then hands finished assets to the `VideoRenderer`
for composition. Never talks to a concrete provider directly.

Will implement/consume:
[`core.interfaces.scene_renderer.SceneRenderer` / `SceneRendererRegistry`](../../core/interfaces/scene_renderer.py),
[`rendering.interfaces.video_renderer.VideoRenderer`](../../rendering/interfaces/video_renderer.py)

No logic implemented in Phase 0 — this package only reserves the import
namespace for Phase 1.
