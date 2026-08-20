# providers/visual/

`HuggingFaceVisualProvider` (`huggingface_provider.py`) — the
`VisualProvider` implementation actually wired into the dashboard, backed
by Hugging Face's free Serverless Inference API
(`stabilityai/stable-diffusion-3-medium-diffusers` by default). Every
prompt is looked up in a content-addressed `LocalCache` (`local_cache.py`,
under `IMAGE_CACHE_DIR` / `./local_cache/images/` by default) before any
API call — image generation is the pipeline's biggest API bottleneck, so
a repeat prompt costs a `stat()` call, not a network round trip. A 503
"model is loading" cold-start response is retried, not treated as
failure; if the API still fails, a local placeholder card
(`placeholder.py`) is generated instead of failing the whole storyboard.

`GeminiImagenProvider` (`gemini_imagen_provider.py`) — the original
`VisualProvider` implementation, backed by Google's Imagen 3. Kept in the
codebase (it's correct and working) but **no longer wired into
`dashboard/app.py`**: Imagen 3 requires a billing-enabled Google Cloud
project even on otherwise-usable free-tier Gemini API keys. See
[`docs/decisions/ADR-004-media-generation-abstraction.md`](../../docs/decisions/ADR-004-media-generation-abstraction.md)
for the full story and exact endpoint/model notes.

Both satisfy [`core.interfaces.visual_provider.VisualProvider`](../../core/interfaces/visual_provider.py).
`AudioProvider` (background-music generation) remains unimplemented.

Called from a `SceneRenderer`, never directly by the orchestrator or
agents — no concrete `SceneRenderer` exists yet (Phase 0 ships zero, see
`core/interfaces/scene_renderer.py`); `dashboard/app.py` calls
`HuggingFaceVisualProvider` directly as a documented hackathon-scope
shortcut around that missing layer.
