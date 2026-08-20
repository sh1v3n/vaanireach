from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.routes import NotImplementedResponse
from core.models import VerificationResult

router = APIRouter(tags=["verification"])


class VerificationListResponse(BaseModel):
    results: list[VerificationResult] = Field(default_factory=list)


@router.get(
    "/projects/{project_id}/verification",
    response_model=VerificationListResponse,
    responses={501: {"model": NotImplementedResponse}},
    status_code=501,
)
async def list_verification_results(project_id: str, claim_id: str | None = None) -> JSONResponse:
    """Will return VerificationResults for the project, optionally filtered
    to one claim. Not implemented in Phase 0."""
    body = NotImplementedResponse(
        detail="list_verification_results not implemented — Phase 0 stub",
        stage="verification",
        project_id=project_id,
    )
    return JSONResponse(status_code=501, content=body.model_dump(mode="json"))
