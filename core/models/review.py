"""Review + Approval — the human-in-the-loop gate. Publication is never
automatic: an Approval with decision=APPROVE is the only thing that may
ever flip `published=True`, and that transition happens outside these
models (backend logic, not implemented in Phase 0)."""
from __future__ import annotations

from datetime import datetime

from pydantic import Field

from core.models.base import IdentifiedModel, _utcnow
from core.models.enums import ApprovalDecision, LanguageCode


class Review(IdentifiedModel):
    project_id: str
    reviewer_id: str
    video_asset_id: str | None = None
    language: LanguageCode
    comments: str | None = None
    flagged_claim_ids: list[str] = Field(default_factory=list)


class Approval(IdentifiedModel):
    project_id: str
    review_id: str | None = None
    decision: ApprovalDecision
    decided_by: str
    reason: str | None = None
    decided_at: datetime = Field(default_factory=_utcnow)
    published: bool = False
    published_at: datetime | None = None
