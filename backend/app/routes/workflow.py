from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.routes import NotImplementedResponse
from core.models import WorkflowEvent

router = APIRouter(tags=["workflow"])


class WorkflowTraceResponse(BaseModel):
    events: list[WorkflowEvent] = Field(default_factory=list)


@router.get(
    "/projects/{project_id}/workflow",
    response_model=WorkflowTraceResponse,
    responses={501: {"model": NotImplementedResponse}},
    status_code=501,
)
async def get_workflow_trace(project_id: str, workflow_run_id: str | None = None) -> JSONResponse:
    """Will return the WorkflowEngine's execution trace (WorkflowEngine.get_trace)
    for the dashboard's audit-trail view. Not implemented in Phase 0."""
    body = NotImplementedResponse(
        detail="get_workflow_trace not implemented — Phase 0 stub",
        stage="workflow_trace",
        project_id=project_id,
    )
    return JSONResponse(status_code=501, content=body.model_dump(mode="json"))
