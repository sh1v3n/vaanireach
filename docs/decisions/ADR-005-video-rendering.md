# ADR-005: Video Rendering / Composition

**Status: DEFERRED pending benchmarking.**

## Context

Final composition (scenes + audio + captions + transitions + branding →
MP4/SRT/VTT) could be done with FFmpeg directly, Remotion (React-based),
MoviePy, or another renderer entirely. Also relevant: **`ffmpeg` is not
currently installed on the primary development machine** — a prerequisite
for any FFmpeg-based path that must be verified before Phase 2 work
starts (`brew install ffmpeg` on macOS).

## Decision

Defer the renderer choice. Ship the interface now:

```python
# rendering/interfaces/video_renderer.py
class VideoRenderer(ABC):
    def render(self, scenes, audio_assets, captions, visual_assets,
               transitions, branding) -> VideoAsset: ...
    def export_captions(self, script, translation, format) -> str: ...
    def get_status(self, job_id: str) -> GenerationStatus: ...
```

A concrete implementation lands in `rendering/adapters/` once chosen;
`rendering/adapters/README.md` is currently an empty placeholder.

## Consequences

- The orchestrator and media agent depend only on `VideoRenderer`, so
  switching from e.g. a quick FFmpeg script to Remotion later doesn't
  ripple outward.
- Before Phase 2 starts, confirm `ffmpeg` availability on whichever
  renderer path is chosen (or explicitly choose a renderer that doesn't
  need it, e.g. a pure-Remotion/Node pipeline).
