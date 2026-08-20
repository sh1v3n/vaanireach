# VaaniReach — Media Provider Strategy

**Status: decided for the hackathon build** — see
[ADR-004](decisions/ADR-004-media-generation-abstraction.md),
[ADR-005](decisions/ADR-005-video-rendering.md), and
[ADR-006](decisions/ADR-006-provider-selection.md) for the full
rationale and the concrete implementations. This document keeps the
original candidate survey below for context; it is no longer a live
decision but a record of what was considered.

## What was actually chosen

| Concern | Provider(s) | Resilience shape |
|---|---|---|
| LLM (facts/scripts/translation/semantic verification) | Google Gemini (`gemini-3.6-flash`) | Horizontal key rotation across `GEMINI_API_KEYS` |
| TTS | Sarvam AI → `edge-tts` | Horizontal rotation, then a hard local fallback |
| Talking-avatar hook | Hedra → D-ID → static local clip | Horizontal rotation per vendor, then a 3rd local fallback tier |
| B-roll images | Google Imagen 3 (`imagen-3.0-generate-002`) | Local cache → horizontal key rotation → local placeholder card |
| Video composition | MoviePy v2 | N/A (local, no external API) |
| Officer review UI | Streamlit (`dashboard/app.py`) | In-process, calls providers directly |

Every one of these was picked specifically so the system degrades to
*something local and free* rather than failing outright when a vendor is
rate-limited, unauthenticated, or simply not configured — see each
provider's module docstring for its exact fallback tiers.

## Why this was originally deferred

The Scene Director / Scene Renderer / Provider split (see
[`architecture.md`](architecture.md#the-visual-strategy-layer-scene-director--scene-renderer--provider))
meant the choice of vendor never had to be made before the rest of the
system was built. That decoupling is exactly what made it possible to
benchmark and swap providers in `providers/` and `rendering/adapters/`
without touching `core/`, `agents/`, or `backend/`.

## Candidate categories considered

- **FFmpeg-based motion graphics** — chosen for final composition (via
  MoviePy, which wraps ffmpeg) — see ADR-005.
- **Image + voice → MP4** — the actual shape of the B-roll pipeline:
  Imagen-generated stills + Ken Burns motion + TTS voiceover.
- **Remotion** — considered, not chosen (Python-native MoviePy fit the
  rest of the stack better).
- **LTX / Hedra / other AI video or avatar APIs** — Hedra chosen as Tier 1
  for the avatar hook, with D-ID as Tier 2.
- **Local/open-source models** — `edge-tts` (TTS) and a locally-rendered
  placeholder clip/image card are the local fallback tiers, not the
  primary path.
- **3D/avatar-based generation** — the avatar hook (`SceneType.AVATAR`)
  is implemented; true 3D scenes (`SceneType.THREE_D`) remain
  experimental/unimplemented.
- **Hybrid** — what shipped: Gemini (text + images) + Sarvam/edge-tts
  (audio) + Hedra/D-ID (avatar) + MoviePy (composition).

## What this means for implementers

Every provider adapter above satisfies one of `VisualProvider`,
`AudioProvider`, `VideoGenerationProvider` (in
[`core/interfaces/`](../core/interfaces/)), or `VideoRenderer` (in
[`rendering/interfaces/`](../rendering/interfaces/)) — and nothing outside
`providers/` or `rendering/adapters/` imports a concrete provider
directly, **except** `dashboard/app.py`, which is the one place in the
codebase that constructs and sequences them (there is no
`WorkflowEngine`/`SceneRenderer` implementation yet — see ADR-004's
"known gap" and `core/interfaces/orchestrator.py`).
