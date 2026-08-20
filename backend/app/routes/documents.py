from __future__ import annotations

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import JSONResponse

from app.routes import NotImplementedResponse
from core.models import Document

router = APIRouter(tags=["documents"])


@router.post(
    "/projects/{project_id}/documents",
    response_model=Document,
    responses={501: {"model": NotImplementedResponse}},
    status_code=501,
)
async def upload_document(project_id: str, file: UploadFile = File(...)) -> JSONResponse:
    """Will validate (app.security.file_validation / filenames), store, and
    register the upload as a Document. Not implemented in Phase 0 — no
    parsing or persistence happens here yet."""
    body = NotImplementedResponse(
        detail="upload_document not implemented — Phase 0 stub",
        stage="document_ingestion",
        project_id=project_id,
    )
    return JSONResponse(status_code=501, content=body.model_dump(mode="json"))
