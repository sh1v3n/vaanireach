"""VaaniReach API entrypoint.

Phase 0: every route below `/health` returns HTTP 501 — this app declares
the API contract (see docs/api-contract.md) but implements no pipeline
logic yet. Run locally with `uvicorn app.main:app --reload` (see
backend/README.md) — no Docker required.
"""
from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

# Must run before importing app.routes: that import transitively pulls in
# provider modules (sarvam_tts_provider.py, cloudflare_provider.py,
# avatar_provider.py) that read os.environ for optional path-override env
# vars at MODULE IMPORT TIME, so .env must already be loaded by then. The
# path is made absolute and independent of cwd — resolving "../.env"
# relative to the current working directory silently no-ops if the backend
# is ever launched from a directory other than backend/.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")  # repo-root .env

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routes import (
    approval,
    documents,
    facts,
    generate,
    pipeline,
    process,
    projects,
    scripts,
    storyboard,
    translate,
    verification,
    workflow,
)

settings = get_settings()

app = FastAPI(
    title="VaaniReach API",
    description="Multilingual Outreach Video Generator — architecture phase, no pipeline runs yet.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router_module in (
    projects,
    documents,
    process,
    facts,
    scripts,
    translate,
    storyboard,
    generate,
    verification,
    workflow,
    approval,
    pipeline,
):
    app.include_router(router_module.router)


@app.get("/health", tags=["health"])
async def health() -> dict:
    return {"status": "ok", "phase": "architecture-only"}
