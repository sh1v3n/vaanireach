"""VaaniReach domain models — plain Pydantic, framework-agnostic.

Import surface for the rest of the codebase, e.g.:
    from core.models import Project, SourceFact, VerificationResult
"""
from core.models.claim import Claim
from core.models.document import Document, DocumentPage
from core.models.enums import (
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
    ProjectStatus,
    SceneType,
    ScriptStatus,
    StoryboardStatus,
    TransitionType,
    TranslationStatus,
    VerificationStatus,
    VerificationType,
    WorkflowRunStatus,
)
from core.models.fact import SourceFact
from core.models.media import AudioAsset, MediaAsset, VideoAsset
from core.models.narrative import NarrativeArc, VisualConcept
from core.models.project import Project
from core.models.review import Approval, Review
from core.models.script import Script
from core.models.storyboard import Scene, Storyboard
from core.models.translation import Translation
from core.models.verification import VerificationResult
from core.models.workflow import WorkflowEvent, WorkflowRun

__all__ = [
    "Project",
    "Document",
    "DocumentPage",
    "SourceFact",
    "Claim",
    "Script",
    "Translation",
    "Storyboard",
    "Scene",
    "NarrativeArc",
    "VisualConcept",
    "MediaAsset",
    "AudioAsset",
    "VideoAsset",
    "VerificationResult",
    "WorkflowRun",
    "WorkflowEvent",
    "Review",
    "Approval",
    # enums
    "LanguageCode",
    "FactType",
    "Criticality",
    "DocumentType",
    "IngestionStatus",
    "ProjectStatus",
    "ScriptStatus",
    "TranslationStatus",
    "StoryboardStatus",
    "SceneType",
    "NarrativeRole",
    "TransitionType",
    "MediaAssetType",
    "GenerationStatus",
    "VerificationType",
    "VerificationStatus",
    "WorkflowRunStatus",
    "PipelineStage",
    "ApprovalDecision",
]
