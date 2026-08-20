"""Instantiate every core domain model with minimal valid data and assert
basic invariants (schema_version, required-field validation). This is a
Phase 0 sanity check, not a behavioral test — there is no behavior yet."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.models import (
    ApprovalDecision,
    Criticality,
    DocumentType,
    FactType,
    GenerationStatus,
    IngestionStatus,
    LanguageCode,
    MediaAssetType,
    NarrativeRole,
    PipelineStage,
    SceneType,
    VerificationStatus,
    VerificationType,
)
from core.models.claim import Claim
from core.models.document import Document, DocumentPage
from core.models.fact import SourceFact
from core.models.media import AudioAsset, MediaAsset, VideoAsset
from core.models.project import Project
from core.models.review import Approval, Review
from core.models.script import Script
from core.models.storyboard import Scene, Storyboard
from core.models.translation import Translation
from core.models.verification import VerificationResult
from core.models.workflow import WorkflowEvent, WorkflowRun
from core.provenance.models import ProvenanceLink, SourceSpan


def _span() -> SourceSpan:
    return SourceSpan(document_id="doc-1", page_number=1, text_span="Farmers receive ₹2,000.")


@pytest.mark.parametrize(
    "build",
    [
        lambda: Project(name="Test Scheme Outreach"),
        lambda: Document(
            project_id="p1",
            filename="a_1234.pdf",
            original_filename="a.pdf",
            file_type=DocumentType.PDF,
            mime_type="application/pdf",
            size_bytes=1024,
            storage_path="p1/doc1/a_1234.pdf",
            checksum_sha256="0" * 64,
        ),
        lambda: DocumentPage(document_id="doc-1", page_number=1, raw_text="Some text."),
        lambda: SourceFact(
            project_id="p1",
            document_id="doc-1",
            fact_type=FactType.AMOUNT,
            value="₹2000",
            raw_text="₹2,000",
            source_span=_span(),
            criticality=Criticality.HIGH,
            confidence=0.95,
            extractor_name="regex-amount-v0",
        ),
        lambda: Claim(
            project_id="p1",
            claim_text="Eligible farmers receive ₹2,000.",
            language=LanguageCode.EN,
            claim_type="amount",
            criticality=Criticality.HIGH,
        ),
        lambda: Script(
            project_id="p1",
            language=LanguageCode.HI,
            audience="general public",
            target_duration_seconds=60,
            narration_text="...",
            generator_name="stub",
        ),
        lambda: Translation(
            project_id="p1",
            script_id="s1",
            language=LanguageCode.MR,
            translated_narration_text="...",
            translation_provider="unselected",
        ),
        lambda: Storyboard(
            project_id="p1", script_id="s1", language=LanguageCode.TA, total_duration_seconds=60.0
        ),
        lambda: Scene(
            storyboard_id="sb1",
            order_index=0,
            scene_type=SceneType.INFOGRAPHIC,
            narrative_role=NarrativeRole.CONTEXT,
            narration_segment_text="...",
            duration_seconds=5.0,
        ),
        lambda: MediaAsset(project_id="p1", asset_type=MediaAssetType.IMAGE),
        lambda: AudioAsset(project_id="p1", language=LanguageCode.HI),
        lambda: VideoAsset(project_id="p1", storyboard_id="sb1", language=LanguageCode.HI),
        lambda: VerificationResult(
            project_id="p1",
            claim_id="c1",
            verification_type=VerificationType.DETERMINISTIC,
            status=VerificationStatus.VERIFIED,
            explanation="Matched SourceFact F-001 exactly.",
            confidence=1.0,
            verifier_name="deterministic-v0",
            is_blocking=False,
        ),
        lambda: WorkflowRun(
            project_id="p1", workflow_name="full_pipeline", current_stage=PipelineStage.FACT_EXTRACTION
        ),
        lambda: WorkflowEvent(
            workflow_run_id="wr1",
            project_id="p1",
            stage=PipelineStage.FACT_EXTRACTION,
            event_type="stage_complete",
            message="23 facts extracted",
        ),
        lambda: Review(project_id="p1", reviewer_id="officer-1", language=LanguageCode.HI),
        lambda: Approval(project_id="p1", decision=ApprovalDecision.APPROVE, decided_by="officer-1"),
        lambda: ProvenanceLink(
            project_id="p1",
            claim_id="c1",
            source_fact_id="F-001",
            source_span=_span(),
            verification_status=VerificationStatus.VERIFIED,
        ),
        _span,
    ],
)
def test_model_instantiates_with_schema_version(build) -> None:
    instance = build()
    assert instance.model_dump()  # round-trips without error
    if hasattr(instance, "schema_version"):
        assert instance.schema_version == 1


def test_generation_status_and_ingestion_status_defaults() -> None:
    doc = Document(
        project_id="p1",
        filename="a.pdf",
        original_filename="a.pdf",
        file_type=DocumentType.PDF,
        mime_type="application/pdf",
        size_bytes=1,
        storage_path="x",
        checksum_sha256="0" * 64,
    )
    assert doc.ingestion_status == IngestionStatus.PENDING

    asset = MediaAsset(project_id="p1", asset_type=MediaAssetType.IMAGE)
    assert asset.generation_status == GenerationStatus.PENDING


def test_required_field_validation_fires() -> None:
    with pytest.raises(ValidationError):
        Project()  # missing required `name`

    with pytest.raises(ValidationError):
        SourceFact(project_id="p1")  # missing most required fields


def test_extra_fields_are_forbidden() -> None:
    with pytest.raises(ValidationError):
        Project(name="x", not_a_real_field="oops")
