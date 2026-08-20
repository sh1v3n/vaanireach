from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.routes import NotImplementedResponse
from core.models import VideoAsset

router = APIRouter(tags=["generate"])


class GenerateMediaRequest(BaseModel):
    storyboard_id: str


@router.post(
    "/projects/{project_id}/generate",
    response_model=VideoAsset,
    responses={501: {"model": NotImplementedResponse}},
    status_code=501,
)
async def generate_media(project_id: str, payload: GenerateMediaRequest) -> JSONResponse:
    """Will dispatch each Scene to a SceneRenderer and compose the final
    video via VideoRenderer. Not implemented in Phase 0 — no media or
    video generation logic runs here."""
    body = NotImplementedResponse(
        detail="generate_media not implemented — Phase 0 stub",
        stage="media_generation",
        project_id=project_id,
    )
    return JSONResponse(status_code=501, content=body.model_dump(mode="json"))
