# ADR-004: Media Generation Abstraction

**Status: DECIDED (Phase 3-4).**

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
| `VisualProvider` | Google Imagen 3 (`imagen-3.0-generate-002`) via `google-genai` | [`providers/visual/gemini_imagen_provider.py`](../../providers/visual/gemini_imagen_provider.py) | Tier 0 content-addressed `LocalCache` (never re-generate the same prompt) → Tier 1 `GeminiManager` horizontal key rotation → Tier 2 local Pillow-drawn placeholder card if every key is exhausted |
| `VideoGenerationProvider` (avatar hook) | Hedra Character-3, then D-ID | [`providers/video/avatar_provider.py`](../../providers/video/avatar_provider.py) | Tier 1 Hedra (horizontal key rotation) → Tier 2 D-ID (horizontal key rotation) → Tier 3 a locally-generated static placeholder clip (`fallback_assets/generic_hook.mp4`) |

`AudioProvider` (background music) remains unimplemented — out of hackathon
scope.

**Known gap:** no concrete `SceneDirector` or `SceneRenderer` exists yet
(`SceneRendererRegistry` still resolves zero renderers). The Phase 5
dashboard (`dashboard/app.py`) calls `GeminiImagenProvider` and
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
