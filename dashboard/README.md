# dashboard/

`app.py` — the Streamlit Officer Review Dashboard (Phase 5). The
in-process front-end for the whole pipeline: it instantiates every
concrete provider from `providers/` and `rendering/adapters/` directly
and sequences Fact Extraction → multilingual Script Generation →
Verification → TTS → Avatar → B-Roll → Video Composition itself, since no
`WorkflowEngine` implementation exists yet (see
`core/interfaces/orchestrator.py`). It never calls `backend/`'s FastAPI
app over HTTP — the two are independent front-ends over the same `core`/
`providers`/`rendering` packages.

## Run it

```bash
# system dependency, no fallback — see ADR-005:
choco install ffmpeg   # Windows, elevated shell; or apt/brew install ffmpeg elsewhere
pip install -r requirements.txt
cp .env.example .env   # fill in GROQ_API_KEYS at minimum; SARVAM/HEDRA/DID_API_KEYS/CLOUDFLARE_* are optional (all have local fallbacks)
streamlit run dashboard/app.py
```

`GROQ_API_KEYS` is the one hard requirement — fact extraction and script
generation have no fallback provider (get a free key at
console.groq.com). `ffmpeg`+`ffprobe` on `PATH` is also a hard
requirement for the final render step (`rendering/adapters/
ffmpeg_video_renderer.py` shells out to them directly, unlike the
TTS/avatar-fallback code paths, which still use moviepy's bundled
`imageio-ffmpeg` binary and need no system install). Everything else
(TTS, avatar, B-roll images) degrades to a local/offline fallback per
`docs/decisions/ADR-004/005/006.md` and the Phase 2-4 provider
docstrings, so the dashboard stays usable even with zero other keys
configured.

## Presenter avatars

Step 2 lets the officer pick a presenter (Male/Female) from the
ready-made headshots in [`avatar/`](../avatar/) — used directly as the
Hedra/D-ID animation source instead of an AI-generated portrait, giving
a consistent, real-looking on-screen presenter with one fewer network
call per render. They're transparent-background PNGs (person cutouts);
`get_presenter_image_path()` flattens each onto a neutral background
once and caches the result as `avatar/avatar_<name>.flattened.jpg`
(gitignored) — Hedra/D-ID need a fully opaque source image.

## Target duration: capped to 30-40s

The sidebar's duration slider only offers 30/35/40s
(`MIN_DURATION_SECONDS`/`MAX_DURATION_SECONDS` in `app.py`) — short-form
by design, not a technical ceiling. `SCRIPT_GENERATION_PROMPT`'s
`target_words` (= duration × 2.3, `groq_provider.py`) already summarizes
the narration to fit that runtime rather than writing a longer script and
truncating the video after the fact.

## Multi-language render: one shared video, audio swapped per language

Step 2's "Languages to render" is a multiselect, not a single choice.
`run_multi_language_render_pipeline()` generates the avatar hook clip and
B-roll images **once**, from a single reference language's script
(English when available, else the first language selected) — both are
purely visual and don't depend on which language's audio ends up under
them. Every other selected language then only needs its own TTS
narration synthesized; that language's audio is swapped onto the shared
avatar clip via `compose_final_video()`'s `hook_audio_path` param
(`rendering/adapters/ffmpeg_video_renderer.py`), which trims or
freeze-extends the (silent) avatar motion to match the new audio's own
length. Net effect: N selected languages cost 1 avatar-animation call +
1 B-roll image set + N TTS/composite passes, not N of everything.

Trade-off, deliberate and documented where it happens: the avatar's lip
movements are phoneme-accurate only for the reference language — every
other language gets the same visual motion with different audio
underneath. This matches what the Tier-3 static fallback already looks
like whenever Hedra/D-ID are unavailable, so it's a low-cost trade in
practice, not a regression from some previously-guaranteed lip-sync.

## Render pipeline parallelism

Within a single render, `run_multi_language_render_pipeline()` runs the
avatar-hook branch (TTS → slice → Hedra/D-ID, reference language only)
and the B-roll branch (prompt drafting → N concurrent Cloudflare calls)
in parallel via `concurrent.futures.ThreadPoolExecutor`, since neither
depends on the other's output — only the final ffmpeg composite needs
both. Worker functions never call `status.write()`/any `st.*` function
themselves (Streamlit UI calls aren't safe off the main script-run
thread) — all progress messages come from the caller, before and after
the parallel section. Step 1 (per-language extraction/scripting/
verification) stays sequential on purpose: it shares one Groq key's
rate-limit budget, and parallel calls there would just make rate-limiting
worse, not faster.
