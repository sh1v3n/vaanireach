from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.routes import NotImplementedResponse
from core.models import LanguageCode, Script

router = APIRouter(tags=["scripts"])


class ScriptGenerateRequest(BaseModel):
    target_language: LanguageCode
    audience: str
    desired_duration_seconds: int


@router.post(
    "/projects/{project_id}/scripts",
    response_model=Script,
    responses={501: {"model": NotImplementedResponse}},
    status_code=501,
)
async def create_script(project_id: str, payload: ScriptGenerateRequest) -> JSONResponse:
    """Will call ScriptGenerator.generate_script grounded in the project's
    Source Fact Ledger. Not implemented in Phase 0."""
    body = NotImplementedResponse(
        detail="create_script not implemented — Phase 0 stub",
        stage="script_generation",
        project_id=project_id,
    )
    return JSONResponse(status_code=501, content=body.model_dump(mode="json"))
