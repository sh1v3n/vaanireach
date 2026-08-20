# ADR-005: Video Rendering / Composition

**Status: DECIDED (Phase 4) — MoviePy.**

## Context

Final composition (scenes + audio + captions + transitions + branding →
MP4/SRT/VTT) could be done with FFmpeg directly, Remotion (React-based),
MoviePy, or another renderer entirely. Also relevant: whether a system
`ffmpeg` install could be assumed on every dev machine.

## Decision

**MoviePy v2** (`moviepy>=2.0`), implemented in
[`rendering/adapters/moviepy_video_renderer.py`](../../rendering/adapters/moviepy_video_renderer.py)
as `MoviePyVideoRenderer(VideoRenderer)`. It resolved the `ffmpeg`
availability question by making it moot: MoviePy pulls in
`imageio-ffmpeg`, which vendors its own ffmpeg binary, so no system
`ffmpeg` install is required on any dev machine.

```python
# rendering/interfaces/video_renderer.py — unchanged from the original design
class VideoRenderer(ABC):
    def render(self, scenes, audio_assets, captions, visual_assets,
               transitions, branding) -> VideoAsset: ...
    def export_captions(self, script, translation, format) -> str: ...
    def get_status(self, job_id: str) -> GenerationStatus: ...
```

What `MoviePyVideoRenderer` actually does — a "news package" composite,
revised 2026-08-20 from an earlier hard cut between an avatar-only scene
and a B-roll-only scene:
- **`compose_final_video()`** (the primary, explicit entry point): ONE
  continuous Ken Burns B-roll background runs the entire video (a
  lead-in segment reusing the first B-roll image covers the avatar
  clip's own duration, then N per-image segments of
  `body_audio.duration / image_count` each) → the avatar clip (Phase 3,
  narration audio already baked in) is overlaid on top as a large,
  bottom-anchored picture-in-picture for exactly its own duration —
  "reporter over B-roll", like a news broadcast, not "reporter, then cut
  to B-roll" → one continuous audio track (the avatar clip's baked-in
  hook audio, then the Phase 2 body audio) → an optional Pillow-drawn
  caption bar burned in over the B-roll portion, after the avatar
  overlay ends (deliberately not MoviePy's `TextClip`, which needs
  either ImageMagick or a resolvable font file as an unverified system
  prerequisite) → `write_videofile(codec="libx264", audio_codec="aac",
  preset="ultrafast")`.
- **`render()`** (the ABC method): derives those same inputs from the
  generic `scenes`/`audio_assets`/`visual_assets` lists (one
  `SceneType.AVATAR` scene = the hook, everything else ordered by
  `order_index` = the B-roll) and delegates to `compose_final_video()`.
- **`export_captions()`**: a naive single-cue SRT/VTT spanning the
  script's full target duration — Phase 0 has no per-word/per-scene
  timing data to build real multi-cue captions from yet.

Validated end-to-end with a pytest smoke test
([`tests/test_phase4_renderer_smoke.py`](../../tests/test_phase4_renderer_smoke.py))
that feeds a dummy avatar clip, 3 dummy images, and a dummy audio track
through both entry points and asserts a playable MP4 comes out with no
codec errors.

## Consequences

- The orchestrator/dashboard depends only on `VideoRenderer`, so swapping
  MoviePy for e.g. Remotion later touches only `rendering/adapters/`.
- Encoding is tuned for hackathon iteration speed (`preset="ultrafast"`),
  not archival quality/file size — revisit if a higher-quality export is
  ever needed.
- No concrete `SceneRenderer` calls `VideoRenderer` yet (see ADR-004's
  known gap) — the Phase 5 dashboard calls `compose_final_video()`
  directly.
