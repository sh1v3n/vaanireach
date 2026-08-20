"""VerificationEngine — the first-class verification component.

Deterministic verification is the designed path for objectively checkable
FactTypes (numbers, dates, amounts, percentages, names, URLs, phone
numbers, locations, scheme names). Semantic verification handles
paraphrases, translated claims, eligibility statements, and other
descriptive claims. `verify_claim` picks the right strategy per claim;
critical claims that come back CONTRADICTED/NOT_FOUND must be flagged
`is_blocking=True` on the resulting VerificationResult.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from core.models.claim import Claim
from core.models.fact import SourceFact
from core.models.verification import VerificationResult


class VerificationEngine(ABC):
    @abstractmethod
    def verify_deterministic(self, claim: Claim, source_facts: list[SourceFact]) -> VerificationResult:
        raise NotImplementedError(
            "VerificationEngine.verify_deterministic not implemented — Phase 0 interface stub"
        )

    @abstractmethod
    def verify_semantic(self, claim: Claim, source_facts: list[SourceFact]) -> VerificationResult:
        raise NotImplementedError(
            "VerificationEngine.verify_semantic not implemented — Phase 0 interface stub"
        )

    @abstractmethod
    def verify_claim(self, claim: Claim, source_facts: list[SourceFact]) -> VerificationResult:
        """Dispatches to verify_deterministic or verify_semantic based on
        the claim's type, then returns a single VerificationResult."""
        raise NotImplementedError(
            "VerificationEngine.verify_claim not implemented — Phase 0 interface stub"
        )

    @abstractmethod
    def verify_batch(self, claims: list[Claim], source_facts: list[SourceFact]) -> list[VerificationResult]:
        raise NotImplementedError(
            "VerificationEngine.verify_batch not implemented — Phase 0 interface stub"
        )
