"""Shared enums used across core models, agents, and interfaces.

Centralized here so `SceneType`, `LanguageCode`, etc. have exactly one
definition — every other module imports from this file rather than
redeclaring values, which is what causes drift between e.g. the Scene
Director's decision and the Scene Renderer's dispatch table.
"""
from __future__ import annotations

from enum import Enum


class LanguageCode(str, Enum):
    """ISO-ish language codes. Extend freely — do NOT treat this as fixed
    at 3 languages. Hindi/Marathi/Tamil are just the initial demo set."""

    EN = "en"
    HI = "hi"
    MR = "mr"
    TA = "ta"
    BN = "bn"
    TE = "te"
    KN = "kn"
    ML = "ml"
    GU = "gu"


class FactType(str, Enum):
    """The fact categories the Source Fact Ledger extracts. Numbers, dates,
    amounts, names, locations, URLs, phone numbers, deadlines, and scheme
    names are all designed for DETERMINISTIC verification (see
    VerificationEngine.verify_deterministic)."""

    PERSON = "person"
    ORGANIZATION = "organization"
    SCHEME = "scheme"
    LOCATION = "location"
    DATE = "date"
    DEADLINE = "deadline"
    AMOUNT = "amount"
    PERCENTAGE = "percentage"
    PHONE_NUMBER = "phone_number"
    URL = "url"
    ELIGIBILITY = "eligibility"
    REQUIREMENT = "requirement"
    STATISTIC = "statistic"
    POLICY = "policy"
    EVENT = "event"


class Criticality(str, Enum):
    """How much a wrong/missing value for this fact would matter if
    published. Critical facts that fail verification must block
    publication — see VerificationResult.is_blocking."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DocumentType(str, Enum):
    PDF = "pdf"
    DOCX = "docx"
    IMAGE = "image"
    TEXT = "text"


class IngestionStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETE = "complete"
    FAILED = "failed"


class ProjectStatus(str, Enum):
    DRAFT = "draft"
    PROCESSING = "processing"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    PUBLISHED = "published"
    REJECTED = "rejected"


class ScriptStatus(str, Enum):
    DRAFT = "draft"
    VERIFIED = "verified"
    FAILED_VERIFICATION = "failed_verification"
    REGENERATING = "regenerating"


class TranslationStatus(str, Enum):
    PENDING = "pending"
    COMPLETE = "complete"
    FAILED = "failed"


class StoryboardStatus(str, Enum):
    DRAFT = "draft"
    READY = "ready"


class SceneType(str, Enum):
    """What VISUAL REPRESENTATION a scene should use. This is a decision
    made by the SceneDirector and is entirely independent of which vendor
    ultimately renders it — see core/interfaces/scene_renderer.py."""

    AI_VIDEO = "ai_video"
    IMAGE_MOTION = "image_motion"
    INFOGRAPHIC = "infographic"
    MAP = "map"
    AVATAR = "avatar"
    THREE_D = "three_d"
    TEXT = "text"
    MIXED = "mixed"


class NarrativeRole(str, Enum):
    """The role a scene plays in the overall story arc, decided by the
    StoryDirector — see core/interfaces/story_director.py. Distinct from
    SceneType: a HOOK and a CTA can both end up SceneType.TEXT, but they
    play very different narrative jobs."""

    HOOK = "hook"
    CONTEXT = "context"
    PROBLEM = "problem"
    ANNOUNCEMENT = "announcement"
    BENEFIT = "benefit"
    ELIGIBILITY = "eligibility"
    HOW_TO = "how_to"
    DEADLINE = "deadline"
    URGENCY = "urgency"
    CTA = "cta"
    CLOSING = "closing"


class TransitionType(str, Enum):
    """How one scene visually hands off to the next. Maps onto ffmpeg's
    xfade filter at Video Composition time (fade/slideleft/zoomin, etc.)
    — no new dependency, ffmpeg (the one hard system dependency) already
    supports this."""

    CUT = "cut"
    FADE = "fade"
    SLIDE = "slide"
    ZOOM = "zoom"


class MediaAssetType(str, Enum):
    IMAGE = "image"
    VIDEO_CLIP = "video_clip"
    AUDIO = "audio"
    ANIMATION = "animation"


class GenerationStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class VerificationType(str, Enum):
    DETERMINISTIC = "deterministic"
    SEMANTIC = "semantic"


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    CONTRADICTED = "contradicted"
    NOT_FOUND = "not_found"
    UNCERTAIN = "uncertain"


class WorkflowRunStatus(str, Enum):
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    COMPLETE = "complete"
    FAILED = "failed"


class PipelineStage(str, Enum):
    """Stages of the pipeline described in docs/workflow.md — used to tag
    WorkflowEvents for the future execution-trace dashboard."""

    DOCUMENT_INGESTION = "document_ingestion"
    DOCUMENT_UNDERSTANDING = "document_understanding"
    FACT_EXTRACTION = "fact_extraction"
    SCRIPT_GENERATION = "script_generation"
    TRANSLATION = "translation"
    CLAIM_EXTRACTION = "claim_extraction"
    VERIFICATION = "verification"
    STORYBOARD_PLANNING = "storyboard_planning"
    MEDIA_GENERATION = "media_generation"
    VIDEO_COMPOSITION = "video_composition"
    FINAL_VERIFICATION = "final_verification"
    HUMAN_REVIEW = "human_review"
    APPROVAL_PUBLICATION = "approval_publication"


class ApprovalDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    REGENERATE = "regenerate"
    EDIT = "edit"
