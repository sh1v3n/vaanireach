from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.routes import NotImplementedResponse
from core.models import Criticality, FactType, SourceFact

router = APIRouter(tags=["facts"])


class FactListResponse(BaseModel):
    facts: list[SourceFact] = Field(default_factory=list)


@router.get(
    "/projects/{project_id}/facts",
    response_model=FactListResponse,
    responses={501: {"model": NotImplementedResponse}},
    status_code=501,
)
async def list_facts(
    project_id: str,
    fact_type: FactType | None = None,
    criticality: Criticality | None = None,
) -> JSONResponse:
    """Will return the project's Source Fact Ledger, optionally filtered.
    Not implemented in Phase 0."""
    body = NotImplementedResponse(
        detail="list_facts not implemented — Phase 0 stub",
        stage="fact_extraction",
        project_id=project_id,
    )
    return JSONResponse(status_code=501, content=body.model_dump(mode="json"))
