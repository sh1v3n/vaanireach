"""Helper for building WorkflowEvent records.

IMPORTANT: `message` must stay a concise, user-facing operational string
("Hindi verification passed", "Marathi verification failed — regenerating")
suitable for an audit-trail UI. Never pass raw model output, prompts, or
chain-of-thought reasoning into this field.
"""
from __future__ import annotations

from typing import Any

from core.models.enums import PipelineStage
from core.models.workflow import WorkflowEvent


def build_event(
    *,
    workflow_run_id: str,
    project_id: str,
    stage: PipelineStage,
    event_type: str,
    message: str,
    agent_name: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> WorkflowEvent:
    return WorkflowEvent(
        workflow_run_id=workflow_run_id,
        project_id=project_id,
        stage=stage,
        agent_name=agent_name,
        event_type=event_type,
        message=message,
        metadata=metadata or {},
    )
