# backend/

FastAPI app for the VaaniReach API. **Phase 0 status: every route except
`GET /health` returns HTTP 501** — this declares the API contract (see
[`../docs/api-contract.md`](../docs/api-contract.md)) without implementing
any document processing, translation, TTS, or video generation logic.

## Run locally (no Docker required)

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=.. uvicorn app.main:app --reload
```

(`PYTHONPATH=..` puts the repo root on the import path so `core/`, `agents/`,
`providers/`, and `rendering/` are importable from `app/`.)

Then visit `http://localhost:8000/docs` for the interactive OpenAPI UI, or:

```bash
curl http://localhost:8000/health
curl -X POST http://localhost:8000/projects -H "Content-Type: application/json" -d '{"name": "test"}'
```

The second call returns `501` with a `{"detail", "stage", "project_id"}` body — expected in Phase 0.

## Tests

```bash
cd backend && source .venv/bin/activate
PYTHONPATH=".:.." pytest ../tests
```
