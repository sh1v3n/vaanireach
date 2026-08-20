# Burned-in captions, avatar PiP lip-sync overlay, and 20-30s video length

**Status:** DRAFT — awaiting user review before implementation planning.
**Scope:** the "template" multilingual pipeline (`providers/narrative/template_story_director.py`
→ `rendering/multilingual_video.py` → `rendering/adapters/ffmpeg_video_renderer.py`), i.e. the
path exercised by `tests/demo_multilingual_video.py`. The separate, older LLM-driven dashboard
pipeline (`dashboard/app.py` + `MoviePyVideoRenderer.compose_final_video()`) is out of scope and
untouched.

## Context

The multilingual pipeline currently produces a Ken-Burns/xfade B-roll video with audio, plus
sidecar `.srt`/`.vtt` caption files — nothing is burned into the video pixels, there is no avatar,
and video length follows however many facts a source document has (currently 30-45s target,
measured at 47s for the sample document). Three changes are needed:

1. Burn per-language captions into the video (bottom bar), keeping the sidecar files too.
2. Add a small lip-synced avatar as a picture-in-picture (PiP) box, bottom-left corner, for the
   full video duration — using the presenter's own narration audio, giving the video a talking
   presenter without changing the existing B-roll visuals.
3. Shorten videos toward a 20-30s target by dropping two scenes that add no new facts.

This repo already has most of the underlying pieces built and tested independently
(`AvatarFailoverProvider` for lip-synced generation with a 3-tier vendor failover,
`build_multi_scene_captions()` for per-scene SRT/VTT) — this design's job is wiring them into the
one path that doesn't use them yet, plus adding the actual pixel compositing (overlay + burn-in),
which doesn't exist yet for this pipeline.

**The avatar is a required part of the intended MVP experience, not a bonus/optional feature.**
The target output is a professional public-information/explainer video with a persistent talking
presenter — story-driven B-roll + multilingual narration + burned-in captions + a lip-synced
presenter avatar in a bottom-left PiP — not a slideshow-with-voiceover that happens to also support
an avatar. The graceful-degradation behavior described under Failure handling below exists solely
as a *reliability safety net* for when the avatar provider or compositing step technically fails at
runtime — it is never a reason to skip, defer, or treat avatar generation as optional during
implementation. Every run attempts full avatar + PiP + caption compositing; only a genuine runtime
failure falls back to captioned-B-roll-only.

**Build/verification priority** for the implementation plan, in order:
1. Base story video (existing `compose_multi_scene()` B-roll + xfade + audio — already working).
2. Full narration audio track (`concat_audio_files()` over the same per-scene audio already used).
3. Avatar lip-sync (`AvatarFailoverProvider.generate_avatar_hook()` against that full track).
4. PiP compositing (avatar overlay onto the B-roll video).
5. Caption burn-in (subtitles bar onto the same composited output).
6. Final verification (fact-verification pass stays as today, plus a real end-to-end check that a
   produced video actually contains the avatar box and burned captions, not just that ffmpeg
   exited 0).

## Decisions (confirmed with user)

- **Avatar placement**: PiP rectangle, bottom-left corner, full video duration — not a full-screen
  avatar, not a short intro-only hook, not a transparent/chroma-keyed floating head (neither Hedra
  nor D-ID expose a background-removal option, so a rectangular box is the honest, buildable
  option). The avatar lip-syncs to the *same* narration audio the B-roll already plays — one audio
  track, not two.
- **Avatar art style**: photorealistic presenter portrait, reusing the existing
  `AVATAR_IMAGE_PROMPT` style already defined in `dashboard/app.py` (not a cartoon/illustrated
  style).
- **Captions**: burned-in as a full-width bottom bar (above the PiP box), no speech-bubble
  treatment. Sidecar `.srt`/`.vtt` generation is kept unchanged alongside the burn-in.
- **Duration**: drop the `CTA` and `CLOSING` scenes from `TemplateStoryDirector` — both are pure
  restatements of facts already spoken earlier (CTA repeats HOW_TO's URL + DEADLINE's date;
  CLOSING repeats ANNOUNCEMENT's scheme name + HOOK's org). No fact is dropped, no scene keeps
  facts unspoken. `TARGET_DURATION_MIN/MAX_SECONDS` become `20.0`/`30.0` (previously `30.0`/`45.0`,
  and previously unenforced anywhere in the codebase). This is best-effort, not a hard cap — a
  source document with many distinct facts may still land a little over 30s, since fact values
  (amounts, URLs, dates, phone numbers) can never be shortened or paraphrased without breaking the
  fact-invention guard that makes every claim traceable to the source document.

## Architecture

```
generate_language_video()  [rendering/multilingual_video.py]
  │
  ├─ translate (unchanged)
  ├─ per-scene TTS (unchanged)
  ├─ FfmpegVideoRenderer.compose_multi_scene()  [unchanged — B-roll + xfade + audio]
  │
  ├─ NEW: concat_audio_files(audio_paths) -> one full-narration WAV
  │        (extracted from FfmpegVideoRenderer._concat_audio, now a shared function
  │         so both compose_multi_scene and the avatar step use the same logic)
  │
  ├─ NEW: AvatarFailoverProvider.generate_avatar_hook(avatar_portrait, full_narration_wav)
  │        (existing provider, unchanged — Hedra -> D-ID -> static-fallback cascade,
  │         audio-driven duration, so a real generation matches the video length)
  │
  ├─ NEW: FfmpegVideoRenderer.compose_pip_and_captions(
  │            broll_video_path, avatar_clip_path, srt_text) -> VideoAsset
  │        one ffmpeg call: PiP overlay (avatar, bottom-left, ~25% width,
  │        `-stream_loop -1` so a short/fallback clip loops to fill the full
  │        duration) + subtitles burn-in (bottom bar, libass, bundled Noto fonts)
  │
  └─ build_multi_scene_captions() sidecar SRT/VTT (unchanged)
```

**Avatar portrait**: a fixed, shared image generated once via `CloudflareVisualProvider` (the
visual provider this pipeline already uses — not `HuggingFaceVisualProvider`, which is the
dashboard pipeline's provider) using the existing `AVATAR_IMAGE_PROMPT` text, under a shared
project id so `LocalCache` serves it from disk on every subsequent call instead of regenerating
per video.

**Failure handling — a reliability safety net, not a design option**: every run *always attempts*
full avatar generation + PiP compositing + caption burn-in; this is never conditionally skipped.
The new step wraps the *existing, already-correct* `compose_multi_scene()` output rather than
replacing it purely so that an unexpected *runtime* failure (e.g. the compositing ffmpeg call
itself erroring on a malformed input) degrades to the plain B-roll+audio video instead of crashing
the whole pipeline — it logs loudly (not silently) when this happens, since a demo run landing in
the degraded path is a signal something is broken and needs fixing, not an accepted steady state.
`AvatarFailoverProvider.generate_avatar_hook()` itself already never raises (it has its own Tier-3
static fallback), so the only new failure surface is the compositing ffmpeg call.

## Components

### `providers/narrative/template_story_director.py`
- Remove the `CTA` scene-generation block (`add_scene(NarrativeRole.CTA, ...)`) and the `CLOSING`
  scene-generation block (`add_scene(NarrativeRole.CLOSING, ...)`) from `plan_narrative_arc()`.
- `TARGET_DURATION_MIN_SECONDS = 20.0`, `TARGET_DURATION_MAX_SECONDS = 30.0`.
- `_ROLE_VISUAL_ELEMENTS`, `_ROLE_VISUAL_BEATS`, `_ROLE_TRANSITION_OUT` entries for `CTA`/`CLOSING`
  are left in place (unused but harmless — `rendering/adapters/cloudflare_scene_renderer.py` still
  supports rendering those scene types directly, and `tests/test_cloudflare_scene_renderer.py`
  exercises that independently of the story director).

### `rendering/adapters/ffmpeg_video_renderer.py`
- Extract the existing `_concat_audio` static method into a module-level `concat_audio_files()`
  function so it's reusable outside `compose_multi_scene`.
- New `compose_pip_and_captions()` function/method: takes the B-roll video path, the avatar clip
  path, and SRT text; returns a `VideoAsset` pointing at the final composited MP4. Single ffmpeg
  invocation combining the `overlay` filter (PiP, bottom-left, scaled, looped) and the `subtitles`
  filter (burn-in, bottom bar, pointed at the bundled Noto font files rather than relying on
  whatever's installed system-wide).
- New bundled assets: two OFL-licensed fonts (Noto Sans, Noto Sans Devanagari) checked into the
  repo (e.g. `fallback_assets/fonts/`) so Hindi/Marathi captions render correctly on any machine,
  not just ones with system Devanagari font coverage already installed.

### `rendering/multilingual_video.py`
- After `compose_multi_scene()`, call `concat_audio_files()` on the same `audio_paths` already
  used for that call (no duplicate TTS), then `AvatarFailoverProvider.generate_avatar_hook()`, then
  `compose_pip_and_captions()`. Wrap the avatar+compositing portion in a broad `try/except` per the
  failure-handling behavior above.
- `LanguageVideoResult` gains no new required fields (the same `video_asset` field now points at
  the composited output when compositing succeeds); may gain an optional flag/field indicating
  whether PiP+caption compositing succeeded, for the demo script to report.

### `dashboard/app.py` (shared asset helper)
- The avatar-portrait-generation pattern (`get_avatar_source_image`, `AVATAR_IMAGE_PROMPT`,
  `SHARED_ASSET_PROJECT_ID`) is duplicated for the template pipeline rather than imported, since
  the dashboard's version is tied to `HuggingFaceVisualProvider` — the new copy targets
  `CloudflareVisualProvider` instead. Exact placement (new small module vs. inline in
  `multilingual_video.py`) is an implementation-time call, not a design constraint.

## Testing

- New compositing test (mirrors `tests/test_phase4_renderer_smoke.py`'s pattern): dummy avatar
  clip + dummy B-roll video + a short SRT through `compose_pip_and_captions()`, asserting a
  playable MP4 comes out with the expected duration and that ffprobe reports no decode errors.
- A Devanagari-script smoke assertion: burning a Hindi/Marathi SRT doesn't error and produces
  non-trivial output file size (glyph-correctness isn't practically assertable automatically, but
  "didn't silently no-op" is).
- `tests/test_narrative_story_director.py::test_target_duration_respected`: update the hardcoded
  `30.0 <= ... <= 45.0` assertion to `20.0 <= ... <= 30.0`.
- New regression test in the same file asserting `CTA`/`CLOSING` never appear in
  `plan_narrative_arc()`'s output, matching the existing style of
  `test_no_duplicate_narrative_roles`/`test_scene_count_is_not_hardcoded`.
- `tests/demo_multilingual_video.py`: extend its per-language SUMMARY output to report whether PiP
  avatar + caption burn-in succeeded, so a manual run stays the fastest way to eyeball the result.
  Per the build-priority list above, a demo run landing on the degraded (no-avatar) path should be
  loudly visible in this output, not quietly indistinguishable from success.

## Out of scope

- The dashboard's separate LLM-driven pipeline (`dashboard/app.py`,
  `MoviePyVideoRenderer.compose_final_video()`) — untouched by this design.
- True transparent/chroma-keyed floating-head avatar (would need background removal/segmentation
  not currently available from either vendor) — noted as a possible future enhancement, not built
  now.
- Speech-bubble-style captions — explicitly declined in favor of the plain bottom bar.
- Hard-enforcing the 20-30s target by further trimming (e.g. splitting the long HOW_TO scene,
  cutting facts) — accepted as best-effort per the duration decision above.
