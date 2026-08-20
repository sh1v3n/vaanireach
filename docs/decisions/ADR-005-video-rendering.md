# ADR-005: Video Rendering / Composition

**Status: DECIDED (Phase 4) — ffmpeg CLI, direct subprocess.**

## Context

Final composition (scenes + audio + captions + transitions + branding →
MP4/SRT/VTT) could be done with FFmpeg directly, Remotion (React-based),
MoviePy, or another renderer entirely. Also relevant: whether a system
`ffmpeg` install could be assumed on every dev machine.

## Decision history

**2026-08-20, MoviePy v2** (`moviepy>=2.0`) was the first concrete
implementation, in `MoviePyVideoRenderer`. It resolved the `ffmpeg`
availability question by making it moot: MoviePy pulls in
`imageio-ffmpeg`, which vendors its own ffmpeg binary, so no system
`ffmpeg` install was required on any dev machine.

**2026-08-21, superseded by ffmpeg CLI** (`FfmpegVideoRenderer`,
[`rendering/adapters/ffmpeg_video_renderer.py`](../../rendering/adapters/ffmpeg_video_renderer.py)).
A second, independently-built pipeline on this project's `main` branch
(`TemplateStoryDirector` → `rendering/multilingual_video.py` →
`rendering/adapters/ffmpeg_video_renderer.py` + `caption_burner.py`) had
already solved the same composition problem via direct ffmpeg subprocess
calls, with two concrete improvements over the MoviePy renderer this
dashboard pipeline had been using:
1. A small, plain, corner-anchored avatar picture-in-picture box instead
   of a large chroma-keyed hero overlay — robust to a vendor watermark
   corrupting the chroma-key mask (see the "Why the avatar overlay is no
   longer chroma-keyed" section below).
2. Short, timed multi-cue captions (correct Devanagari/Bengali glyph
   support) instead of one caption bar holding the entire script for the
   whole B-roll segment.

Rather than duplicate that engineering, this dashboard's own
`FfmpegVideoRenderer` ports those two mechanics from `main`, adapted onto
this branch's own pipeline shape (one narration script + N B-roll images
per language, avatar speaking only a 5-second hook — see
`dashboard/app.py`'s module docstring) rather than `main`'s different
`TemplateStoryDirector`/multi-scene structure, which this branch does
NOT adopt. The `ffmpeg` availability tradeoff MoviePy avoided is
accepted here deliberately: a real dev/demo machine needs system
`ffmpeg`+`ffprobe` on `PATH`.

```python
# rendering/interfaces/video_renderer.py — unchanged from the original design
class VideoRenderer(ABC):
    def render(self, scenes, audio_assets, captions, visual_assets,
               transitions, branding) -> VideoAsset: ...
    def export_captions(self, script, translation, format) -> str: ...
    def get_status(self, job_id: str) -> GenerationStatus: ...
```

What `FfmpegVideoRenderer` actually does:
- **`compose_final_video()`** (the primary, explicit entry point): ONE
  continuous Ken Burns B-roll background (via ffmpeg's `zoompan` filter)
  runs the entire video — a lead-in segment reusing the first B-roll
  image covers the avatar hook's own duration, then N per-image segments
  of `body_audio.duration / image_count` each — chained via short
  `xfade` crossfades (not hard cuts) → the avatar clip (Phase 3,
  narration audio already baked in), fitted to exactly the hook's
  duration, is overlaid as a small (`PIP_WIDTH=200px`), bottom-left,
  plain rectangular picture-in-picture, visible only for
  `enable='lte(t,hook_duration)'` — matching `main`'s own "small box,
  bottom-left, plain background" shape exactly — → one continuous audio
  track (the hook's own audio, then the Phase 2 body audio,
  concatenated via an `aresample`+`concat` filter graph that survives
  mismatched sample rates/channel counts) → short, timed caption cues
  (`rendering/adapters/caption_burner.py`, max 2 lines each, dark bottom
  gradient card, correct Devanagari/Bengali font) burned in over the
  FULL timeline, not just the B-roll portion → muxed with
  `libx264`/`aac`/`preset=ultrafast`.
  - Optional `hook_audio_path` param (unchanged behavior from the old
    renderer): swaps the avatar clip's baked-in audio for a different
    track — e.g. another language's own narration — and fits the
    (silent) avatar motion to that track's own duration via
    `_fit_video_to_duration` (ffmpeg `-t` trim if shorter, `tpad`
    freeze-on-last-frame if longer, never loop the talking motion from
    the start). Lets one avatar-animation call be reused across every
    selected language, at the documented cost of lip sync no longer
    being phoneme-accurate for the non-reference languages.
- **`render()`** (the ABC method): derives those same inputs from the
  generic `scenes`/`audio_assets`/`visual_assets` lists (one
  `SceneType.AVATAR` scene = the hook, everything else ordered by
  `order_index` = the B-roll) and delegates to `compose_final_video()`.
- **`export_captions()`**: real multi-cue SRT/VTT, built from the same
  `caption_burner.split_narration_into_cues()` segmentation actually
  burned into the video (an upgrade over the old renderer's single-cue,
  whole-script placeholder).

### Why the avatar overlay is no longer chroma-keyed

The MoviePy renderer tried to key the presenter photo's flat backdrop
out via `vfx.MaskColor` (`AVATAR_CHROMA_KEY_COLOR` matching
`dashboard/app.py`'s `PRESENTER_BACKGROUND_RGB`) so the overlay would
blend into the B-roll, with a dimmed/offset silhouette as a cheap drop
shadow. That mask depends on the backdrop staying a known, uniform
color. Confirmed live (`.audit/03-video-hook.png`, captured during this
project's own verification pass): D-ID's trial-tier watermark (a tiled
"D-iD" mark) is NOT a uniform color and is baked into every frame,
including the backdrop — so pixels near each watermark glyph fell
outside the chroma key's color-distance threshold and stayed opaque,
and the "cleaned up" overlay shipped the watermark anyway, riding
through what was supposed to remove exactly that kind of artifact.

`main`'s independent `FfmpegVideoRenderer` had already made a different,
more robust design choice: don't try to make the box disappear — make it
small and corner-anchored instead, the same shape a real news broadcast
PiP inset actually has (which IS expected to look like a box, border and
all). This is robust to a corrupted backdrop instead of depending on it
staying clean, so this ADR adopts the same choice. **This does not
itself detect or strip a vendor watermark** — see
`providers/video/avatar_provider.py`'s module docstring for the current,
still-open Hedra/D-ID account-blocker state; a genuinely watermark-free
result still depends on a funded/non-trial vendor account.

Validated end-to-end with a pytest smoke test
([`tests/test_phase4_renderer_smoke.py`](../../tests/test_phase4_renderer_smoke.py))
that feeds a dummy avatar clip, 3 dummy images, and a dummy audio track
through both entry points and asserts a playable MP4 comes out with no
codec errors, at the correct 720x1280 resolution and total duration.

## Consequences

- The orchestrator/dashboard depends only on `VideoRenderer`, so swapping
  the renderer again later touches only `rendering/adapters/`.
- System `ffmpeg`+`ffprobe` on `PATH` is now a hard prerequisite — a dev
  machine without it cannot render, full stop (no MoviePy/imageio-ffmpeg
  fallback). Document this in onboarding/setup instructions.
- Encoding is tuned for hackathon iteration speed (`preset="ultrafast"`),
  not archival quality/file size — revisit if a higher-quality export is
  ever needed.
- No concrete `SceneRenderer` calls `VideoRenderer` yet (see ADR-004's
  known gap) — the Phase 5 dashboard calls `compose_final_video()`
  directly.
