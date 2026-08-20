# rendering/adapters/

`FfmpegVideoRenderer` (`ffmpeg_video_renderer.py`) — the concrete
`VideoRenderer` for the hackathon build (ADR-005 itself remains formally
deferred; nothing benchmarked the alternatives). Replaced
`MoviePyVideoRenderer` on 2026-08-21 with the video-generation mechanics
(format, avatar box, captions) ported from the `main` branch's separately
built `TemplateStoryDirector` pipeline — see the module docstring for the
full rationale and what changed. Requires system `ffmpeg`/`ffprobe` on
`PATH` (no fallback, by design); this is the one hard system dependency
this pipeline has.

Implements the Assembly Sequence from `docs/workflow.md`'s Video
Composition stage: ONE continuous Ken Burns B-roll background (each image
given a subtle zoom, chained via short `xfade` crossfades — a lead-in
segment reuses the first image for the avatar hook's own duration) runs
the whole video, with the Phase 3 avatar hook clip (audio already baked
in) overlaid as a small, plain, bottom-left picture-in-picture box for
exactly its own duration, then gone — not the old chroma-keyed hero
overlay (see below for why). The Phase 2 `body_audio.wav` plays under the
B-roll portion, and short, timed caption cues (never the whole script in
one block) are burned in over the full timeline via
[`caption_burner.py`](./caption_burner.py).

`compose_final_video()` is the primary entry point (explicit file paths
in, one `VideoAsset` out); `render()` implements the generic
`VideoRenderer` ABC by deriving those paths from a `Scene`/`AudioAsset`/
`MediaAsset` list — see the class docstring for the exact derivation rule.

`compose_final_video()`'s optional `hook_audio_path` swaps the avatar
clip's own baked-in audio for a different track and refits the (silent)
avatar motion to that track's duration (`_fit_video_to_duration`: trim if
shorter, freeze-on-last-frame via ffmpeg's `tpad` filter if longer) — how
`dashboard/app.py`'s `run_multi_language_render_pipeline()` reuses one
avatar animation + B-roll set across every selected language, swapping
in just that language's own narration audio instead of re-rendering the
avatar per language.

**Why the avatar overlay is no longer chroma-keyed**: the old
`MoviePyVideoRenderer` tried to key the presenter photo's flat backdrop
out via `vfx.MaskColor` so the overlay would blend into the B-roll. That
mask depends on the backdrop staying a known, uniform color — when a
vendor's watermark (D-ID's tiled "D-iD" mark, confirmed live) corrupted
that uniformity, the mask failed and the watermark rode straight through
the "cleaned up" overlay, which read worse than the plain-rectangle
problem it was meant to fix (see `.audit/03-video-hook.png` from that
investigation). `FfmpegVideoRenderer` instead uses a small (200px wide),
honestly-a-box corner PiP — the same "small box, bottom-left, plain
background" shape a real news broadcast inset actually has — which is
robust to a corrupted backdrop instead of depending on it being clean.
This does not itself detect or strip a vendor watermark; see
`providers/video/avatar_provider.py`'s module docstring for the current,
still-open account-blocker state of both vendors.

Satisfies [`rendering.interfaces.VideoRenderer`](../interfaces/video_renderer.py).
Nothing in `core/`, `agents/`, or `backend/` should ever import directly
from this directory — only through `rendering.interfaces.VideoRenderer`.
