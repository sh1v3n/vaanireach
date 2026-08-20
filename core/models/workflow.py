"""WorkflowRun + WorkflowEvent — the agentic orchestration's execution
trace. WorkflowEvent.message is a concise, user-facing operational string
(e.g. "Marathi verification failed, regeneration triggered") — it must
NEVER contain raw model chain-of-thought."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from core.models.base import IdentifiedModel, _utcnow
from core.models.enums import PipelineStage, WorkflowRunStatus


class WorkflowRun(IdentifiedModel):
    project_id: str
    workflow_name: str
    status: WorkflowRunStatus = WorkflowRunStatus.RUNNING
    started_at: datetime = Field(default_factory=_utcnow)
    completed_at: datetime | None = None
    current_stage: PipelineStage
    event_ids: list[str] = Field(default_factory=list)


class WorkflowEvent(IdentifiedModel):
    workflow_run_id: str
    project_id: str
    stage: PipelineStage
    agent_name: str | None = None
    event_type: str
    message: str
    """Concise operational description for a user-facing audit trail.
    Never model reasoning/chain-of-thought."""
    timestamp: datetime = Field(default_factory=_utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)
