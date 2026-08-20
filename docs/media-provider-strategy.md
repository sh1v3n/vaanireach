# VaaniReach — Media Provider Strategy

**The video/visual generation provider is intentionally undecided.** This
document exists to name candidate categories and evaluation criteria for
later benchmarking — none of it is referenced anywhere in code. See
[ADR-004](decisions/ADR-004-media-generation-abstraction.md),
[ADR-005](decisions/ADR-005-video-rendering.md), and
[ADR-006](decisions/ADR-006-provider-selection.md) for the formal
deferred decisions and the interface contracts any future choice must
satisfy.

## Why this is deferred

The Scene Director / Scene Renderer / Provider split (see
[`architecture.md`](architecture.md#the-visual-strategy-layer-scene-director--scene-renderer--provider))
means the choice of vendor never needs to be made before the rest of the
system is built. Committing early would risk locking the whole pipeline
to one vendor's pricing, latency, quality, or Indian-language support —
all of which are still open questions this early in the project.

## Candidate categories (examples only — none selected)

- **FFmpeg-based motion graphics** — compose static assets + Ken Burns/pan
  effects programmatically. Cheapest, most controllable, most manual.
- **Image + voice → MP4** — generate a still image per scene, layer TTS
  audio, animate minimally.
- **Remotion** — React-based programmatic video composition.
- **LTX / Hedra / other AI video or avatar APIs** — highest visual
  fidelity, highest cost/latency/uncertainty, ToS considerations.
- **Local/open-source models** — no per-call cost, but hardware/latency
  constraints on a laptop during a 24-hour hackathon.
- **3D/avatar-based generation** — highest production value, highest
  complexity; treated as experimental (see `docs/TODO.md`).
- **Hybrid** — e.g. FFmpeg composition for most scenes, an AI video API
  only for a hero/intro scene.

## Evaluation criteria (to be filled in once benchmarking happens)

| Criterion | Notes |
|---|---|
| Cost per generated video | Per-scene or per-minute pricing |
| Latency | Can it run within a demo's time budget? |
| Output quality | Visual fidelity, consistency across scenes |
| Indian-language voice/text support | Critical — this is the whole point of the project |
| Licensing / ToS | Can outputs be publicly distributed? |
| Self-hosted vs. API | Offline capability, data residency |
| Integration effort | How much adapter code against `core/interfaces` |

## What this means for implementers

Any future provider adapter must implement one of:
`VisualProvider`, `AudioProvider`, `VideoGenerationProvider` (in
[`core/interfaces/`](../core/interfaces/)), or `VideoRenderer` (in
[`rendering/interfaces/`](../rendering/interfaces/)) — and nothing outside
`providers/` or `rendering/adapters/` should ever import it directly.
