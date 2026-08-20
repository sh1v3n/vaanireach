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
pip install -r requirements.txt
cp .env.example .env   # fill in GROQ_API_KEYS at minimum; SARVAM/HEDRA/DID_API_KEYS/CLOUDFLARE_* are optional (all have local fallbacks)
streamlit run dashboard/app.py
```

`GROQ_API_KEYS` is the one hard requirement — fact extraction and script
generation have no fallback provider (get a free key at
console.groq.com). Everything else (TTS, avatar, B-roll images) degrades
to a local/offline fallback per `docs/decisions/ADR-004/005/006.md` and
the Phase 2-4 provider docstrings, so the dashboard stays usable even
with zero other keys configured.

## Presenter avatars

Step 2 lets the officer pick a presenter (Male/Female) from the
ready-made headshots in [`avatar/`](../avatar/) — used directly as the
Hedra/D-ID animation source instead of an AI-generated portrait, giving
a consistent, real-looking on-screen presenter with one fewer network
call per render. They're transparent-background PNGs (person cutouts);
`get_presenter_image_path()` flattens each onto a neutral background
once and caches the result as `avatar/avatar_<name>.flattened.jpg`
(gitignored) — Hedra/D-ID need a fully opaque source image.

## Render pipeline parallelism

`run_render_pipeline()` runs the avatar-hook branch (TTS → slice →
Hedra/D-ID) and the B-roll branch (prompt drafting → N concurrent
Cloudflare calls) in parallel via `concurrent.futures.ThreadPoolExecutor`,
since neither depends on the other's output — only the final MoviePy
composite needs both. Worker functions never call `status.write()`/any
`st.*` function themselves (Streamlit UI calls aren't safe off the main
script-run thread) — all progress messages come from the caller, before
and after the parallel section. Step 1 (per-language extraction/
scripting/verification) stays sequential on purpose: it shares one Groq
key's rate-limit budget, and parallel calls there would just make
rate-limiting worse, not faster.
