from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.routes import NotImplementedResponse
from core.models import LanguageCode, Storyboard

router = APIRouter(tags=["storyboard"])


class StoryboardRequest(BaseModel):
    script_id: str
    language: LanguageCode


@router.post(
    "/projects/{project_id}/storyboard",
    response_model=Storyboard,
    responses={501: {"model": NotImplementedResponse}},
    status_code=501,
)
async def create_storyboard(project_id: str, payload: StoryboardRequest) -> JSONResponse:
    """Will call SceneDirector.plan_storyboard on a verified Script. Not
    implemented in Phase 0."""
    body = NotImplementedResponse(
        detail="create_storyboard not implemented — Phase 0 stub",
        stage="storyboard_planning",
        project_id=project_id,
    )
    return JSONResponse(status_code=501, content=body.model_dump(mode="json"))
