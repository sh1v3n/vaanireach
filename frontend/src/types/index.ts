/**
 * Provisional TypeScript mirrors of core/models/*.py, for the future
 * dashboard's typing. These are duplicated-with-backend by hand for now —
 * Phase 1+ should consider generating this file from the FastAPI OpenAPI
 * schema instead of maintaining it manually.
 */

export type LanguageCode = "en" | "hi" | "mr" | "ta" | "bn" | "te" | "kn" | "ml" | "gu";

export type FactType =
  | "person"
  | "organization"
  | "scheme"
  | "location"
  | "date"
  | "deadline"
  | "amount"
  | "percentage"
  | "phone_number"
  | "url"
  | "eligibility"
  | "requirement"
  | "statistic"
  | "policy"
  | "event";

export type Criticality = "low" | "medium" | "high" | "critical";

export type SceneType =
  | "ai_video"
  | "image_motion"
  | "infographic"
  | "map"
  | "avatar"
  | "three_d"
  | "text"
  | "mixed";

export type VerificationStatus = "verified" | "contradicted" | "not_found" | "uncertain";

export type ApprovalDecision = "approve" | "reject" | "regenerate" | "edit";

export interface SourceSpan {
  document_id: string;
  page_number: number;
  text_span: string;
  section_heading?: string | null;
  paragraph_index?: number | null;
}

export interface Project {
  id: string;
  name: string;
  description?: string | null;
  target_languages: LanguageCode[];
  status: string;
}

export interface SourceFact {
  id: string;
  project_id: string;
  document_id: string;
  fact_type: FactType;
  value: string;
  raw_text: string;
  source_span: SourceSpan;
  criticality: Criticality;
  confidence: number;
}

export interface Claim {
  id: string;
  project_id: string;
  claim_text: string;
  language: LanguageCode;
  source_fact_ids: string[];
  claim_type: string;
  criticality: Criticality;
}

export interface VerificationResult {
  id: string;
  claim_id: string;
  status: VerificationStatus;
  matched_source_fact_ids: string[];
  explanation: string;
  is_blocking: boolean;
}

export interface WorkflowEvent {
  id: string;
  stage: string;
  event_type: string;
  message: string;
  timestamp: string;
}

export type NarrativeRole =
  | "hook"
  | "context"
  | "problem"
  | "announcement"
  | "benefit"
  | "eligibility"
  | "how_to"
  | "deadline"
  | "urgency"
  | "cta"
  | "closing";

export type JobStatus = "pending" | "running" | "pending_review" | "failed";
export type LanguageJobStatus = "pending_review" | "published" | "rejected";

export interface PipelineScene {
  id: string;
  order_index: number;
  narrative_role: NarrativeRole;
  narration_segment_text: string;
  source_fact_ids: string[];
  claim_ids: string[]; // join key into LanguageJobView.verification_results[].claim_id
}

export interface PipelineVerificationResult {
  claim_id: string;
  status: VerificationStatus;
  is_blocking: boolean;
  explanation: string;
}

export interface LanguageJobView {
  status: LanguageJobStatus;
  regenerating: boolean;
  error: string | null;
  avatar_tier: number | null;
  avatar_composited: boolean;
  video_url: string;
  srt_url: string;
  vtt_url: string;
  scenes: PipelineScene[];
  verified_count: number;
  blocking_count: number;
  verification_results: PipelineVerificationResult[];
}

export interface PipelineFact {
  id: string;
  fact_type: FactType;
  value: string;
  raw_text: string;
  source_span: { page_number: number; text_span: string };
}

export type PipelineStage =
  | "extracting_facts"
  | "facts_extracted"
  | "planning_narrative"
  | "drafting_narration"
  | "rendering_images"
  | "generating_video";

export type VoiceGender = "male" | "female";
export type NarrationStyle = "news" | "storytelling";

export interface Voice {
  speaker: string;
  gender: VoiceGender;
  supports_pitch: boolean; // only true for the smaller bulbul:v2 set — Sarvam has no pitch control on v3
}

export interface JobVoiceSettings {
  speaker: string;
  gender: VoiceGender;
  style: NarrationStyle;
  pace: number;
  pitch: number | null;
}

export interface JobView {
  job_id: string;
  status: JobStatus;
  stage: PipelineStage | null;
  error: string | null;
  languages: Record<LanguageCode, LanguageJobView>;
  facts: PipelineFact[];
  voice: JobVoiceSettings;
}
