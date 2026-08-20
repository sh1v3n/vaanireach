"""FastAPI route stubs.

Every route below declares its real request/response contract using the
core Pydantic domain models directly (so the contract is
implementation-independent — see docs/api-contract.md) but the handler
body immediately returns HTTP 501. No document processing, translation,
TTS, or video generation logic is implemented in Phase 0.
"""
from __future__ import annotations

from pydantic import BaseModel


class NotImplementedResponse(BaseModel):
    detail: str
    stage: str
    project_id: str | None = None
