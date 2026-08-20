# providers/visual/

`PollinationsVisualProvider` (`pollinations_visual_provider.py`) — the
`VisualProvider` implementation actually wired into the dashboard, backed
by [Pollinations.ai](https://pollinations.ai)'s free, keyless REST image
API. No account, no API key, no billing. Every prompt is looked up in a
content-addressed `LocalCache` (`local_cache.py`, under `IMAGE_CACHE_DIR`
/ `./local_cache/images/` by default) before any API call — image
generation is the pipeline's biggest API bottleneck, so a repeat prompt
costs a `stat()` call, not a network round trip. Retries with backoff on
a transient failure; if the API still fails, a local placeholder card
(`placeholder.py`) is generated instead of failing the whole storyboard.

Two earlier `VisualProvider` implementations are kept in the codebase
(correct, working, just no longer wired in) — both dropped for the same
underlying reason: **`HuggingFaceVisualProvider`** (`huggingface_provider.py`,
Hugging Face's free Inference API) and **`GeminiImagenProvider`**
(`gemini_imagen_provider.py`, Google Imagen 3). Google requires a
billing-enabled Cloud project even on free-tier Gemini keys; Hugging
Face's free `hf-inference` tier and Together AI (one of the backends its
Inference Providers router can pick) both started gating image
generation behind billing/deposit too. See
[`docs/decisions/ADR-004-media-generation-abstraction.md`](../../docs/decisions/ADR-004-media-generation-abstraction.md)
for the full history.

All three satisfy [`core.interfaces.visual_provider.VisualProvider`](../../core/interfaces/visual_provider.py).
`AudioProvider` (background-music generation) remains unimplemented.

Called from a `SceneRenderer`, never directly by the orchestrator or
agents — no concrete `SceneRenderer` exists yet (Phase 0 ships zero, see
`core/interfaces/scene_renderer.py`); `dashboard/app.py` calls
`PollinationsVisualProvider` directly as a documented hackathon-scope
shortcut around that missing layer.
