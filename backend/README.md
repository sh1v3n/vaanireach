# backend/

FastAPI app for the VaaniReach API. Most routes (`documents`, `facts`,
`generate`, `process`, `projects`, `scripts`, `storyboard`, `translate`,
`verification`, `workflow`, `approval`) are still Phase 0 stubs returning
HTTP 501 — see [`../docs/api-contract.md`](../docs/api-contract.md). The
`pipeline` router (`/pipeline/jobs/...`) is real: it wraps
`rendering.multilingual_video.run_full_pipeline` end to end (document text
in, review-and-approve job out) — see
`../docs/superpowers/specs/2026-08-21-review-dashboard-frontend-design.md`.

Because `pipeline` imports the real generation pipeline, this app's actual
import-time dependencies go beyond `backend/requirements.txt` — it also
needs the root-level `requirements.txt` (Groq/Cloudflare/Sarvam/D-ID/Hedra
clients, ffmpeg/moviepy compositing, etc.), and the repo-root `.env` for
provider API keys.

## Run locally (no Docker required)

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r ../requirements.txt
PYTHONPATH=.. uvicorn app.main:app --reload
```

(`PYTHONPATH=..` puts the repo root on the import path so `core/`, `agents/`,
`providers/`, and `rendering/` are importable from `app/`. The app loads the
repo-root `.env` itself on startup — see `app/main.py`.)

Then visit `http://localhost:8000/docs` for the interactive OpenAPI UI, or:

```bash
curl http://localhost:8000/health
curl -X POST http://localhost:8000/pipeline/jobs -F "languages=en" -F "text=A short test notice."
```

`/projects`, `/documents`, etc. still return `501` — expected, those stubs
are unrelated to the real `/pipeline/*` API above.

## Tests

```bash
cd backend && source .venv/bin/activate
PYTHONPATH=".:.." pytest ../tests
```
