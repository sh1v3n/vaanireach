# providers/visual/

`GeminiImagenProvider` (`gemini_imagen_provider.py`) — a `VisualProvider`
implementation backed by Google's Imagen 3 (`imagen-3.0-generate-002`),
reusing Phase 1's `GeminiManager` for horizontal key-rotation resilience.
Every prompt is looked up in a content-addressed `LocalCache`
(`local_cache.py`, under `IMAGE_CACHE_DIR` / `./local_cache/images/` by
default) before any API call — image generation is the pipeline's biggest
API bottleneck, so a repeat prompt costs a `stat()` call, not a network
round trip. If every Gemini key is exhausted, a local placeholder card is
generated instead of failing the whole storyboard.

Satisfies [`core.interfaces.visual_provider.VisualProvider`](../../core/interfaces/visual_provider.py).
`AudioProvider` (background-music generation) remains unimplemented — see
[`docs/decisions/ADR-004-media-generation-abstraction.md`](../../docs/decisions/ADR-004-media-generation-abstraction.md).

Called from a `SceneRenderer`, never directly by the orchestrator or
agents — no concrete `SceneRenderer` exists yet (Phase 0 ships zero, see
`core/interfaces/scene_renderer.py`).
