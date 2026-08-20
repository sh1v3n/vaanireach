# ADR-004: Media Generation Abstraction

**Status: DECIDED (Phase 3-4); B-roll/avatar-image provider revised three
times on 2026-08-20 (Gemini Imagen → Hugging Face → Pollinations.ai →
Cloudflare Workers AI, the permanent choice) — see the table and revision
notes below.**

## Context

Visual/media generation for outreach videos can be approached many ways —
FFmpeg-based motion graphics, image+voice compositing, Remotion, AI video
APIs (Hedra/Runway/LTX-style), local/open-source models, 3D/avatar
generation, or hybrids. None had been benchmarked yet for cost, latency,
quality, or Indian-language support. Committing early risked locking the
whole pipeline to one vendor's constraints before those trade-offs were
understood.

## Decision

Deferred the choice originally; shipped three independently swappable
interface layers first (see `docs/architecture.md#the-visual-strategy-layer`):

```python
# core/interfaces/scene_director.py
class SceneDirector(ABC):
    def choose_scene_type(self, fact: SourceFact, claim: Claim) -> SceneType: ...
    def plan_storyboard(self, script: Script) -> Storyboard: ...

# core/interfaces/scene_renderer.py
class SceneRenderer(ABC):
    def supports(self, scene_type: SceneType) -> bool: ...
    def render_scene(self, scene: Scene) -> MediaAsset: ...

# core/interfaces/visual_provider.py / audio_provider.py / video_provider.py
class VisualProvider(ABC):
    def generate_image(self, prompt: str, scene: Scene) -> MediaAsset: ...
    def get_status(self, job_id: str) -> GenerationStatus: ...
    def cancel(self, job_id: str) -> None: ...

class VideoGenerationProvider(ABC):
    def generate_scene(self, scene: Scene) -> MediaAsset: ...
    def generate_video(self, storyboard: Storyboard, audio_assets: list[AudioAsset]) -> VideoAsset: ...
    def get_status(self, job_id: str) -> GenerationStatus: ...
    def cancel(self, job_id: str) -> None: ...
```

Providers were then picked for the hackathon build:

| Interface | Provider | File | Resilience shape |
|---|---|---|---|
| `VisualProvider` | Cloudflare Workers AI (`@cf/black-forest-labs/flux-1-schnell`) | [`providers/visual/cloudflare_flux_provider.py`](../../providers/visual/cloudflare_flux_provider.py) | Tier 0 content-addressed `LocalCache` (never re-generate the same prompt) → Tier 1 `CloudflareFluxManager` horizontal token rotation → Tier 2 local Pillow-drawn placeholder card if every token is exhausted |
| `VideoGenerationProvider` (avatar hook) | Hedra Character-3, then D-ID | [`providers/video/avatar_provider.py`](../../providers/video/avatar_provider.py) | Tier 1 Hedra (horizontal key rotation) → Tier 2 D-ID (horizontal key rotation) → Tier 3 a locally-generated static placeholder clip (`fallback_assets/generic_hook.mp4`) |

`AudioProvider` (background music) remains unimplemented — out of hackathon
scope.

**Revision 1 (2026-08-20): `VisualProvider` moved off Gemini Imagen 3.**
`providers/visual/gemini_imagen_provider.py` (`GeminiImagenProvider`) is
the originally-shipped implementation and is kept in the codebase — it's
a correct, working `VisualProvider` — but is no longer wired into
`dashboard/app.py`. Reason: Google's Imagen 3 (`imagen-3.0-generate-002`)
requires a billing-enabled Google Cloud project even on Gemini API keys
that are otherwise usable free-tier; every `generate_images` call 404'd
("not supported for predict") against every key configured for this
project, confirmed live while running the pipeline end-to-end against a
real document. Hugging Face's free Inference API needs only a free
account + access token, no billing.

**Revision 2 (2026-08-20, same day): `VisualProvider` moved off Hugging
Face too, onto Pollinations.ai.** `providers/visual/huggingface_provider.py`
(`HuggingFaceVisualProvider`) is likewise kept in the codebase,
correct and working, but no longer wired in. Reason: Hugging Face's
free `hf-inference` tier and Together AI (one of the backends its
Inference Providers router can route a model to) both started gating
image generation behind a billing/deposit requirement — the identical
problem that forced Revision 1, just with a different vendor. See
`HuggingFaceVisualProvider`'s module docstring for the exact
endpoint/model history along the way (Hugging Face retired its old
`api-inference.huggingface.co` host and now routes through
`router.huggingface.co`; only a small, changing subset of models are
free on the `hf-inference` tier). `PollinationsVisualProvider`
(`providers/visual/pollinations_visual_provider.py`) needs no API key
and no billing at all — a plain unauthenticated GET request.

**Revision 3 (2026-08-20, same day): `VisualProvider` moved onto
Cloudflare Workers AI — the project's permanent choice.**
`providers/visual/pollinations_visual_provider.py`
(`PollinationsVisualProvider`) is likewise kept in the codebase,
correct and working, but no longer wired in. Reason: unlike the first
two revisions, this wasn't forced by a billing wall — Pollinations.ai's
free public queue worked, just not as the long-term answer. Cloudflare
Workers AI's `@cf/black-forest-labs/flux-1-schnell` is a distilled
few-step model built specifically for low latency (Cloudflare's own
recommended `steps=4`), served from Cloudflare's edge network, and
produced noticeably higher-quality, artifact-free output than every
prior provider tried — confirmed side-by-side during live testing.
`CloudflareFluxVisualProvider` (`providers/visual/cloudflare_flux_provider.py`)
needs a Cloudflare account id and one or more API tokens
(`CLOUDFLARE_ACCOUNT_ID` / `CLOUDFLARE_API_TOKENS`), rotated horizontally
via `CloudflareFluxManager` (`providers/visual/cloudflare_flux_client.py`)
on a 429/auth error — the same `itertools.cycle` + cooldown shape as
`GeminiManager`/`HedraAvatarManager`/`SarvamTTSManager`.

**Known gap:** no concrete `SceneDirector` or `SceneRenderer` exists yet
(`SceneRendererRegistry` still resolves zero renderers). The Phase 5
dashboard (`dashboard/app.py`) calls `CloudflareFluxVisualProvider` and
`AvatarFailoverProvider` directly rather than through a `SceneRenderer`,
manually constructing the `Scene` objects each call needs. This is a
documented, deliberate shortcut for the hackathon timeline, not a
reversal of the layering decision — a real `SceneDirector`/`SceneRenderer`
can be inserted later without touching either provider.

## Consequences

- Benchmarking happened in parallel with the rest of the build; the
  providers above only touch `providers/` — nothing in `core/`, `agents/`,
  or `backend/` imports a concrete provider.
- Multiple `SceneRenderer`s can still coexist later (e.g. a different
  provider for one hero scene) without special-casing anywhere else, once
  that layer is actually built.
- The dashboard's direct-call shortcut is the one place today that would
  need to change if/when a real `SceneDirector`/`SceneRenderer` lands.
