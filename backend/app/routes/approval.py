from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.routes import NotImplementedResponse
from core.models import Approval

router = APIRouter(tags=["approval"])


class ApprovalRequest(BaseModel):
    video_asset_id: str
    decided_by: str
    reason: str | None = None


class RejectRequest(BaseModel):
    video_asset_id: str
    decided_by: str
    reason: str | None = None


@router.post(
    "/projects/{project_id}/approve",
    response_model=Approval,
    responses={501: {"model": NotImplementedResponse}},
    status_code=501,
)
async def approve_project(project_id: str, payload: ApprovalRequest) -> JSONResponse:
    """Will record an Approval(decision=APPROVE) and — only then — allow
    publication. Publication is never automatic. Not implemented in
    Phase 0."""
    body = NotImplementedResponse(
        detail="approve_project not implemented — Phase 0 stub",
        stage="approval",
        project_id=project_id,
    )
    return JSONResponse(status_code=501, content=body.model_dump(mode="json"))


@router.post(
    "/projects/{project_id}/reject",
    response_model=Approval,
    responses={501: {"model": NotImplementedResponse}},
    status_code=501,
)
async def reject_project(project_id: str, payload: RejectRequest) -> JSONResponse:
    """Will record an Approval(decision=REJECT). Not implemented in
    Phase 0."""
    body = NotImplementedResponse(
        detail="reject_project not implemented — Phase 0 stub",
        stage="approval",
        project_id=project_id,
    )
    return JSONResponse(status_code=501, content=body.model_dump(mode="json"))
