# ADR-004: Media Generation Abstraction

**Status: DEFERRED pending benchmarking.**

## Context

Visual/media generation for outreach videos can be approached many ways —
FFmpeg-based motion graphics, image+voice compositing, Remotion, AI video
APIs (Hedra/Runway/LTX-style), local/open-source models, 3D/avatar
generation, or hybrids. None has been benchmarked yet for cost, latency,
quality, or Indian-language support. Committing early would lock the
whole pipeline to one vendor's constraints before those trade-offs are
understood.

## Decision

Defer the choice. Instead, ship three independently swappable interface
layers now (see `docs/architecture.md#the-visual-strategy-layer`):

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

Any future provider — FFmpeg, Remotion, LTX, Hedra, a local model, or
something not yet released — must satisfy `VisualProvider`,
`AudioProvider`, or `VideoGenerationProvider`, and is only ever called
from inside a `SceneRenderer`. `SceneDirector`, the orchestrator,
verification, and the dashboard never import a concrete provider.

## Consequences

- Benchmarking can happen in parallel with the rest of the build; slotting
  in a winner later touches only `providers/` and `rendering/adapters/`.
- Multiple `SceneRenderer`s can coexist (e.g. FFmpeg for most scenes, an
  AI video API for one hero scene) without special-casing anywhere else.
