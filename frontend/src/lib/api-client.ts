/**
 * Typed API client stub. Every function below points at the backend's
 * (currently 501-stubbed) contract — see docs/api-contract.md. None of
 * these are wired to real UI yet; they exist so the dashboard's data
 * layer can be built against a stable shape once the backend is real.
 */
import type { Project, SourceFact, VerificationResult, WorkflowEvent } from "@/types";

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

export { API_BASE_URL };
