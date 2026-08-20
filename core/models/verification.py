"""VerificationResult — the output of the Verification Engine for one
Claim. `is_blocking` is what stops publication: a CRITICAL-criticality
claim that comes back CONTRADICTED or NOT_FOUND must set this True."""
from __future__ import annotations

from datetime import datetime

from pydantic import Field

from core.models.base import IdentifiedModel, _utcnow
from core.models.enums import VerificationStatus, VerificationType


class VerificationResult(IdentifiedModel):
    project_id: str
    claim_id: str
    verification_type: VerificationType
    status: VerificationStatus
    matched_source_fact_ids: list[str] = Field(default_factory=list)
    explanation: str
    confidence: float = Field(ge=0.0, le=1.0)
    verifier_name: str
    is_blocking: bool
    verified_at: datetime = Field(default_factory=_utcnow)
