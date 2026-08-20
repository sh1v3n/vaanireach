"""WorkflowEngine — the agentic orchestration contract described in
docs/workflow.md:

    extract facts -> detect critical facts -> generate script -> verify
        -> if failed: regenerate -> verify again -> proceed

Agents (agents/*) are the things that make real decisions or select tools;
the orchestrator sequences them and emits WorkflowEvents so the pipeline's
progress is auditable in a dashboard without exposing model reasoning.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from core.models.claim import Claim
from core.models.enums import LanguageCode
from core.models.fact import SourceFact
from core.models.script import Script
from core.models.verification import VerificationResult
from core.models.workflow import WorkflowEvent, WorkflowRun


class WorkflowEngine(ABC):
    @abstractmethod
    def run_pipeline(self, project_id: str) -> WorkflowRun:
        raise NotImplementedError(
            "WorkflowEngine.run_pipeline not implemented — Phase 0 interface stub"
        )

    @abstractmethod
    def extract_facts_stage(self, project_id: str) -> list[SourceFact]:
        raise NotImplementedError(
            "WorkflowEngine.extract_facts_stage not implemented — Phase 0 interface stub"
        )

    @abstractmethod
    def generate_script_stage(self, project_id: str, target_language: LanguageCode) -> Script:
        raise NotImplementedError(
            "WorkflowEngine.generate_script_stage not implemented — Phase 0 interface stub"
        )

    @abstractmethod
    def verify_stage(self, project_id: str, claims: list[Claim]) -> list[VerificationResult]:
        raise NotImplementedError(
            "WorkflowEngine.verify_stage not implemented — Phase 0 interface stub"
        )

    @abstractmethod
    def regenerate_on_failure(
        self,
        project_id: str,
        script: Script,
        failed_results: list[VerificationResult],
    ) -> Script:
        raise NotImplementedError(
            "WorkflowEngine.regenerate_on_failure not implemented — Phase 0 interface stub"
        )

    @abstractmethod
    def emit_event(self, event: WorkflowEvent) -> None:
        raise NotImplementedError(
            "WorkflowEngine.emit_event not implemented — Phase 0 interface stub"
        )

    @abstractmethod
    def get_trace(self, workflow_run_id: str) -> list[WorkflowEvent]:
        raise NotImplementedError(
            "WorkflowEngine.get_trace not implemented — Phase 0 interface stub"
        )
