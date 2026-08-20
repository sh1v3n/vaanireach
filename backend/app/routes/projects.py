from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.routes import NotImplementedResponse
from core.models import LanguageCode, Project

router = APIRouter(tags=["projects"])


class ProjectCreateRequest(BaseModel):
    name: str
    description: str | None = None
    target_languages: list[LanguageCode] = Field(default_factory=list)


@router.post(
    "/projects",
    response_model=Project,
    responses={501: {"model": NotImplementedResponse}},
    status_code=501,
)
async def create_project(payload: ProjectCreateRequest) -> JSONResponse:
    """Will construct a Project and persist it. Not implemented in Phase 0."""
    body = NotImplementedResponse(
        detail="create_project not implemented — Phase 0 stub",
        stage="project_setup",
    )
    return JSONResponse(status_code=501, content=body.model_dump(mode="json"))
