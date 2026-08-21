/**
 * Typed API client stub. Every function below points at the backend's
 * (currently 501-stubbed) contract — see docs/api-contract.md. None of
 * these are wired to real UI yet; they exist so the dashboard's data
 * layer can be built against a stable shape once the backend is real.
 */
import type { Project, SourceFact, VerificationResult, WorkflowEvent, JobView, LanguageCode } from "@/types";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

function notImplemented(name: string): never {
  throw new Error(
    `api-client.${name} not implemented — Phase 0 stub (backend route also returns 501)`
  );
}

export async function createProject(_input: { name: string }): Promise<Project> {
  return notImplemented("createProject");
}

export async function listFacts(_projectId: string): Promise<SourceFact[]> {
  return notImplemented("listFacts");
}

export async function listVerificationResults(_projectId: string): Promise<VerificationResult[]> {
  return notImplemented("listVerificationResults");
}

export async function getWorkflowTrace(_projectId: string): Promise<WorkflowEvent[]> {
  return notImplemented("getWorkflowTrace");
}

export async function createJob(input: { languages: LanguageCode[]; file?: File; text?: string }): Promise<{ job_id: string; status: string }> {
  const form = new FormData();
  for (const lang of input.languages) form.append("languages", lang);
  if (input.file) form.append("file", input.file);
  if (input.text) form.append("text", input.text);

  const resp = await fetch(`${API_BASE_URL}/pipeline/jobs`, { method: "POST", body: form });
  if (!resp.ok) throw new Error(`createJob failed: ${resp.status} ${await resp.text()}`);
  return resp.json();
}

export async function getJob(jobId: string): Promise<JobView> {
  const resp = await fetch(`${API_BASE_URL}/pipeline/jobs/${jobId}`);
  if (!resp.ok) throw new Error(`getJob failed: ${resp.status} ${await resp.text()}`);
  return resp.json();
}

export async function approveLanguage(jobId: string, language: LanguageCode): Promise<void> {
  const resp = await fetch(`${API_BASE_URL}/pipeline/jobs/${jobId}/languages/${language}/approve`, { method: "POST" });
  if (!resp.ok) throw new Error(`approveLanguage failed: ${resp.status} ${await resp.text()}`);
}

export async function rejectLanguage(jobId: string, language: LanguageCode): Promise<void> {
  const resp = await fetch(`${API_BASE_URL}/pipeline/jobs/${jobId}/languages/${language}/reject`, { method: "POST" });
  if (!resp.ok) throw new Error(`rejectLanguage failed: ${resp.status} ${await resp.text()}`);
}

export async function editScene(
  jobId: string, language: LanguageCode, sceneId: string, narrationSegmentText: string,
): Promise<{ scene_id: string; narration_segment_text: string; verification: { status: string; is_blocking: boolean; explanation: string } }> {
  const resp = await fetch(`${API_BASE_URL}/pipeline/jobs/${jobId}/languages/${language}/edit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scene_id: sceneId, narration_segment_text: narrationSegmentText }),
  });
  if (!resp.ok) throw new Error(`editScene failed: ${resp.status} ${await resp.text()}`);
  return resp.json();
}

export async function regenerateLanguage(jobId: string, language: LanguageCode): Promise<void> {
  const resp = await fetch(`${API_BASE_URL}/pipeline/jobs/${jobId}/languages/${language}/regenerate`, { method: "POST" });
  if (!resp.ok) throw new Error(`regenerateLanguage failed: ${resp.status} ${await resp.text()}`);
}

export { API_BASE_URL };
