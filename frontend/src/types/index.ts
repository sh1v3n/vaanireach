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
