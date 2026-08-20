# VaaniReach — API Contract

> Phase 0: every endpoint below except `GET /health` returns **HTTP 501**
> with a `NotImplementedResponse` body (`{"detail", "stage", "project_id"}`).
> Request/response models are declared using the real `core/models`
> Pydantic domain models directly, so the contract is stable and
> implementation-independent — see [`backend/app/routes/`](../backend/app/routes/).

| Method | Path | Request Model | Response Model | Stub `stage` |
|---|---|---|---|---|
| POST | `/projects` | `ProjectCreateRequest` | `Project` | `project_setup` |
| POST | `/projects/{id}/documents` | multipart upload (`UploadFile`) | `Document` | `document_ingestion` |
| POST | `/projects/{id}/process` | `ProcessRequest` | `WorkflowRun` | `pipeline_orchestration` |
| GET | `/projects/{id}/facts` | query: `fact_type?`, `criticality?` | `FactListResponse{facts: SourceFact[]}` | `fact_extraction` |
| POST | `/projects/{id}/scripts` | `ScriptGenerateRequest` | `Script` | `script_generation` |
| POST | `/projects/{id}/translate` | `TranslateRequest` | `TranslationListResponse{translations: Translation[]}` | `translation` |
| POST | `/projects/{id}/storyboard` | `StoryboardRequest` | `Storyboard` | `storyboard_planning` |
| POST | `/projects/{id}/generate` | `GenerateMediaRequest` | `VideoAsset` | `media_generation` |
| GET | `/projects/{id}/verification` | query: `claim_id?` | `VerificationListResponse{results: VerificationResult[]}` | `verification` |
| GET | `/projects/{id}/workflow` | query: `workflow_run_id?` | `WorkflowTraceResponse{events: WorkflowEvent[]}` | `workflow_trace` |
| POST | `/projects/{id}/approve` | `ApprovalRequest` | `Approval` | `approval` |
| POST | `/projects/{id}/reject` | `RejectRequest` | `Approval` | `approval` |

Plus `GET /health` → `{"status": "ok", "phase": "architecture-only"}` (the
only endpoint that actually works today).

## Example: current stub response

```
$ curl -X POST http://localhost:8000/projects -d '{"name": "test"}' -H 'Content-Type: application/json'

HTTP/1.1 501 Not Implemented
{
  "detail": "create_project not implemented — Phase 0 stub",
  "stage": "project_setup",
  "project_id": null
}
```

## Design notes

- Every route file documents in its docstring which `core/interfaces`
  method it will eventually call, so Phase 1 wiring is mechanical.
- No document processing, translation, TTS, or video generation logic is
  implemented anywhere in Phase 0 — routes exist purely to fix the
  contract shape.
- File upload validation (`app/security/file_validation.py`,
  `app/security/filenames.py`) is implemented for real, since it's cheap
  and provider-independent, but the upload route itself is still stubbed
  (no storage or persistence happens yet).
