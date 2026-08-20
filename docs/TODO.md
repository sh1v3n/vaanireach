# VaaniReach — Implementation Backlog

No GitHub Issues are used for this — the repo has a remote but issue
tracking wasn't part of the Phase 0 scope. This checklist is the backlog.

## MUST HAVE (24-hour hackathon core)

- [ ] Document ingestion (PDF at minimum; DOCX/image nice-to-have within must-have scope)
- [ ] Fact extraction (`FactExtractor` implementation(s) for the `FactType`s in scope)
- [ ] Source Fact Ledger populated and queryable (`GET /projects/{id}/facts` working)
- [ ] Multilingual script generation grounded in the ledger
- [ ] At least 3 Indian languages end-to-end
- [ ] Verification (deterministic at minimum; semantic for eligibility/paraphrase claims)
- [ ] Storyboard / scene planning (`SceneDirector.plan_storyboard` working)
- [ ] Media generation abstraction wired to at least one real `SceneRenderer` + provider
- [ ] Video composition (`VideoRenderer` producing an actual MP4)
- [ ] Human review / approval gate (publication never automatic)

## NICE TO HAVE

- [ ] Fact-level highlighting in the dashboard (`ProvenanceLink` rendered visually)
- [ ] Operational workflow execution trace shown in the dashboard
- [ ] Subtitle export (SRT/VTT)
- [ ] Automatic visual selection from approved sources
- [ ] Additional languages beyond the initial 3

## EXPERIMENTAL

- [ ] Avatar generation (`SceneType.AVATAR`)
- [ ] AI video generation (`SceneType.AI_VIDEO`)
- [ ] 3D scenes (`SceneType.THREE_D`)
- [ ] MCP media tools (translation/TTS/image/video/rendering/verification exposed via MCP)

## Phase map (for reference)

- **Phase 0 — Architecture** (this): repository, models, interfaces, docs. ✅ done.
- **Phase 1 — Core MVP**: PDF → facts → script → translation → verification.
- **Phase 2 — Media**: storyboard → visual assets → TTS → video composition.
- **Phase 3 — Dashboard**: source → generated content → verification → approval.
- **Phase 4 — Differentiators**: everything in NICE TO HAVE / EXPERIMENTAL above.

## Phase 0 follow-ups (small, scoped)

- [ ] Add Dockerfiles for `backend/` and `frontend/` if the team wants
      `docker-compose.yml` to actually work (currently a placeholder — see
      the file's header comment).
- [ ] Decide whether `backend/app/db_models.py` (SQLModel table classes)
      lands at the start of Phase 1 or once the first real pipeline stage
      needs persistence.
