from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.routes import NotImplementedResponse
from core.models import LanguageCode, Translation

router = APIRouter(tags=["translate"])


class TranslateRequest(BaseModel):
    script_id: str
    target_languages: list[LanguageCode] = Field(default_factory=list)


class TranslationListResponse(BaseModel):
    translations: list[Translation] = Field(default_factory=list)


@router.post(
    "/projects/{project_id}/translate",
    response_model=TranslationListResponse,
    responses={501: {"model": NotImplementedResponse}},
    status_code=501,
)
async def translate_script(project_id: str, payload: TranslateRequest) -> JSONResponse:
    """Will fan a Script out to each target language via TranslationProvider.
    Not implemented in Phase 0."""
    body = NotImplementedResponse(
        detail="translate_script not implemented — Phase 0 stub",
        stage="translation",
        project_id=project_id,
    )
    return JSONResponse(status_code=501, content=body.model_dump(mode="json"))
