from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.routes import NotImplementedResponse
from core.models import WorkflowRun

router = APIRouter(tags=["process"])


class ProcessRequest(BaseModel):
    """Reserved for future pipeline configuration (e.g. which stages to
    run). Empty in Phase 0."""


@router.post(
    "/projects/{project_id}/process",
    response_model=WorkflowRun,
    responses={501: {"model": NotImplementedResponse}},
    status_code=501,
)
async def process_project(project_id: str, payload: ProcessRequest) -> JSONResponse:
    """Will invoke WorkflowEngine.run_pipeline. Not implemented in Phase 0."""
    body = NotImplementedResponse(
        detail="process_project not implemented — Phase 0 stub",
        stage="pipeline_orchestration",
        project_id=project_id,
    )
    return JSONResponse(status_code=501, content=body.model_dump(mode="json"))
