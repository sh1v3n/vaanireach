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
cp .env.example .env   # fill in GEMINI_API_KEYS at minimum; SARVAM/HEDRA/DID_API_KEYS are optional (all have local fallbacks)
streamlit run dashboard/app.py
```

`GEMINI_API_KEYS` is the one hard requirement — fact extraction and
script generation have no fallback provider. Everything else (TTS,
avatar, B-roll images) degrades to a local/offline fallback per
`docs/decisions/ADR-004/005/006.md` and the Phase 2-4 provider
docstrings, so the dashboard stays usable even with zero other keys
configured.
