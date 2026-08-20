# providers/visual/

`CloudflareFluxVisualProvider` (`cloudflare_flux_provider.py`, backed by
`cloudflare_flux_client.py`'s `CloudflareFluxManager`) — the `VisualProvider`
implementation actually wired into the dashboard, and the project's
**permanent** image-generation provider. Runs Cloudflare Workers AI's
`@cf/black-forest-labs/flux-1-schnell` — a distilled few-step model built
for low latency, served from Cloudflare's edge network. Every prompt is
looked up in a content-addressed `LocalCache` (`local_cache.py`, under
`IMAGE_CACHE_DIR` / `./local_cache/images/` by default) before any API
call — image generation is the pipeline's biggest API bottleneck, so a
repeat prompt costs a `stat()` call, not a network round trip.
`CloudflareFluxManager` rotates across `CLOUDFLARE_API_TOKENS` on a
429/auth error (same `itertools.cycle` + cooldown shape as
`GeminiManager`/`HedraAvatarManager`/`SarvamTTSManager`); if Cloudflare is
unconfigured or every token is exhausted, a local placeholder card
(`placeholder.py`) is generated instead of failing the whole storyboard.

Three earlier `VisualProvider` implementations are kept in the codebase
(correct, working, just no longer wired in), each dropped along the way
to Cloudflare: **`GeminiImagenProvider`** (`gemini_imagen_provider.py`,
Google Imagen 3 — needs a billing-enabled Cloud project even on
free-tier Gemini keys), **`HuggingFaceVisualProvider`**
(`huggingface_provider.py`, Hugging Face's free Inference API — its free
`hf-inference` tier, and Together AI, one of the backends its router can
pick, also started gating image generation behind billing/deposit), and
**`PollinationsVisualProvider`** (`pollinations_visual_provider.py`,
Pollinations.ai — worked fine, just not chosen as the permanent
provider). See
[`docs/decisions/ADR-004-media-generation-abstraction.md`](../../docs/decisions/ADR-004-media-generation-abstraction.md)
for the full history.

All four satisfy [`core.interfaces.visual_provider.VisualProvider`](../../core/interfaces/visual_provider.py).
`AudioProvider` (background-music generation) remains unimplemented.

Called from a `SceneRenderer`, never directly by the orchestrator or
agents — no concrete `SceneRenderer` exists yet (Phase 0 ships zero, see
`core/interfaces/scene_renderer.py`); `dashboard/app.py` calls
`CloudflareFluxVisualProvider` directly as a documented hackathon-scope
shortcut around that missing layer.
