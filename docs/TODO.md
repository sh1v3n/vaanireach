# VaaniReach — Implementation Backlog

No GitHub Issues are used for this — the repo has a remote but issue
tracking wasn't part of the Phase 0 scope. This checklist is the backlog.

## MUST HAVE (24-hour hackathon core)

- [x] Document ingestion — **plain-text notice only** (pasted or `.txt` upload
      via the dashboard); PDF/DOCX/OCR parsing (`core/interfaces/document_parser.py`)
      is still unimplemented.
- [x] Fact extraction (`GeminiLLMProvider.extract_facts`, Gemini-backed)
- [x] Source Fact Ledger populated — via the dashboard's Fact Ledger table,
      not yet via `GET /projects/{id}/facts` (backend routes are still 501 stubs)
- [x] Multilingual script generation grounded in the ledger (`generate_script_with_claims`)
- [x] 4 Indian languages end-to-end (English, Hindi, Marathi, Bengali — see `dashboard/app.py`)
- [x] Verification — deterministic (`rapidfuzz`) + semantic (Gemini), with an
      automatic regenerate-on-blocking-failure pass
- [ ] Storyboard / scene planning (`SceneDirector.plan_storyboard`) — still
      unimplemented; the dashboard constructs `Scene` objects by hand instead
- [~] Media generation abstraction — providers are wired and working
      (`GeminiImagenProvider`, `AvatarFailoverProvider`), but no concrete
      `SceneRenderer`/`SceneRendererRegistry` calls them yet (see ADR-004)
- [x] Video composition (`MoviePyVideoRenderer` producing a real MP4 + SRT)
- [x] Human review / approval gate — the dashboard's "Approve & Render" step;
      publication is never automatic (no upload/publish code path exists)

## NICE TO HAVE

- [ ] Fact-level highlighting in the dashboard (`ProvenanceLink` rendered visually)
- [ ] Operational workflow execution trace shown in the dashboard (no
      `WorkflowEngine`/`WorkflowEvent` emission yet — see `docs/architecture.md`)
- [x] Subtitle export (SRT) — `MoviePyVideoRenderer.export_captions`, downloadable from the dashboard
- [ ] Automatic visual selection from approved sources
- [x] Additional languages beyond the initial 3 (4 shipped; `LanguageCode` supports 9)

## EXPERIMENTAL

- [x] Avatar generation (`SceneType.AVATAR`) — Hedra → D-ID → local fallback, `providers/video/avatar_provider.py`
- [ ] AI video generation (`SceneType.AI_VIDEO`)
- [ ] 3D scenes (`SceneType.THREE_D`)
- [ ] MCP media tools (translation/TTS/image/video/rendering/verification exposed via MCP)

## Phase map (for reference)

- **Phase 0 — Architecture**: repository, models, interfaces, docs. ✅ done.
- **Phase 1 — LLM provider**: Gemini-backed fact extraction, script
  generation, translation, verification. ✅ done.
- **Phase 2 — TTS**: Sarvam + edge-tts fallback, hook/body audio slicing. ✅ done.
- **Phase 3 — Avatar**: Hedra → D-ID → local-fallback 3-tier resilience. ✅ done.
- **Phase 4 — Visual + Rendering**: Gemini Imagen B-roll + local cache,
  MoviePy video compositing (Ken Burns, captions). ✅ done.
- **Phase 5 — Dashboard**: Streamlit officer review UI, in-process
  pipeline orchestration, source → generated content → verification →
  approve & render → download. ✅ done.
- **Not yet built**: `SceneDirector`/`SceneRenderer` (ADR-004's known
  gap), `WorkflowEngine`/`agents/*` (orchestration/event-trace layer),
  document parsing beyond plain text, the FastAPI `backend/` routes
  (still 501 stubs — the dashboard bypasses them entirely).

## Phase 0 follow-ups (small, scoped)

- [ ] Add Dockerfiles for `backend/` and `frontend/` if the team wants
      `docker-compose.yml` to actually work (currently a placeholder — see
      the file's header comment).
- [ ] Decide whether `backend/app/db_models.py` (SQLModel table classes)
      lands at the start of Phase 1 or once the first real pipeline stage
      needs persistence.
