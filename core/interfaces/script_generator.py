"""ScriptGenerator — produces narration grounded in the Source Fact Ledger.
The source document remains authoritative: this interface takes facts as
input and must be able to regenerate when verification fails, but it never
becomes the source of truth itself."""
from __future__ import annotations

from abc import ABC, abstractmethod

from core.models.enums import LanguageCode
from core.models.fact import SourceFact
from core.models.script import Script
from core.models.verification import VerificationResult


class ScriptGenerator(ABC):
    @abstractmethod
    def generate_script(
        self,
        source_facts: list[SourceFact],
        source_context: str,
        target_language: LanguageCode,
        audience: str,
        desired_duration_seconds: int,
    ) -> Script:
        raise NotImplementedError(
            "ScriptGenerator.generate_script not implemented — Phase 0 interface stub"
        )

    @abstractmethod
    def regenerate_script(
        self,
        previous_script: Script,
        verification_results: list[VerificationResult],
    ) -> Script:
        """Called when verification fails on a previous script version —
        must address the specific CONTRADICTED/NOT_FOUND claims."""
        raise NotImplementedError(
            "ScriptGenerator.regenerate_script not implemented — Phase 0 interface stub"
        )
