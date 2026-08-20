# VaaniReach — Multilingual Outreach Video Generator

**Team:** non technical

> **Current status: architecture phase.** This repository defines the
> system's data models, interfaces, and API contract. **No document
> processing, translation, TTS, or video generation pipeline is
> implemented or runnable yet.** Every API route except `GET /health`
> returns HTTP 501 by design — see [Current Status](#current-status) below
> for exactly what does and doesn't work today.

## Problem

Government and institutional announcements often reach only a fraction of
their intended audience because they are text-heavy and frequently
available only in English. Converting announcements into multiple Indian
languages and accessible video formats manually is slow and difficult to
scale.

## Proposed solution

A multimodal AI generation and verification pipeline with an agentic
workflow: a document is ingested, facts are extracted into a structured
**Source Fact Ledger**, a narration script is generated and translated
into multiple Indian languages, every generated claim is verified against
the ledger (deterministically for numbers/dates/amounts/etc., semantically
for paraphrases and eligibility statements), a storyboard is planned, and
finally a video is composed — with **human approval required before
anything is published.** Anything that can't be verified is flagged, not
invented.

The video-generation technology itself is **deliberately undecided** —
see [Future Media-Generation Options](#future-media-generation-options).

## Architecture

```mermaid
flowchart LR
    A[Official Document] --> B[Document Ingestion]
    B --> C[Document Understanding]
    C --> D[Fact Extraction]
    D --> E[Source Fact Ledger]
    E --> F[Script Generation]
    F --> G[Multilingual Translation]
    G --> H[Claim Extraction]
    H --> I[Fact Verification]
    I -->|failed| F
    I -->|passed| J[Storyboard / Scene Planning]
    J --> K[Visual + Audio Generation]
    K --> L[Video Composition]
    L --> M[Final Verification]
    M --> N[Human Review]
    N --> O[Approval / Publication]
```

Full write-up: [`docs/architecture.md`](docs/architecture.md). Also see
[`docs/workflow.md`](docs/workflow.md) (execution trace + verify/regenerate
loop), [`docs/data-model.md`](docs/data-model.md) (entity relationships),
[`docs/api-contract.md`](docs/api-contract.md) (endpoint contracts), and
[`docs/decisions/`](docs/decisions/) (ADRs, including the deferred
media-provider decisions).

### Key architectural principle

> VaaniReach is not coupled to a particular video-generation technology.
> The AI Scene Director decides how information should be visually
> communicated, while provider adapters handle the actual media
> generation/rendering.

## Core workflow

1. **Document Intelligence** — ingest PDF/DOCX/image sources, extract
   page-level text/headings/tables with page-and-span provenance.
2. **Source Fact Ledger** — structured, provenance-tagged facts (amounts,
   dates, deadlines, names, locations, eligibility, …) become the single
   source of truth.
3. **Script Generation + Translation** — narration grounded in the
   ledger, generated for at least 3 Indian languages (extensible to more).
4. **Verification** — every generated claim is checked
   deterministically or semantically against the ledger; critical
   unverified/contradicted claims block publication.
5. **Storyboard / Scene Planning** — a Scene Director chooses *what*
   visual representation fits each fact (map, infographic, animated
   number, …), independent of *which* vendor renders it.
6. **Media Generation + Composition** — visual/audio assets are produced
   and composed into a final video with captions.
7. **Human Review + Approval** — an officer reviews source, generated
   content, and verification results, then Approves / Rejects /
   Regenerates / Edits. **Publication is never automatic.**

## Technology stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3.12 + FastAPI + Pydantic v2 | Fast to build, strong typing, matches the domain-model-first approach |
| Domain models | Plain Pydantic (`core/models/`) | Framework-agnostic — reusable by agents, tests, a future CLI |
| Persistence (Phase 1+) | SQLite via SQLModel | Zero-setup local dev; SQLModel unifies Pydantic + SQLAlchemy |
| Frontend | Next.js (TypeScript, App Router) | Standard React tooling for the future Review Dashboard |
| Media generation | **Undecided** | See [Future Media-Generation Options](#future-media-generation-options) |
| Local dev | Native Python venv + npm | Runs directly on macOS/Linux, no Docker required |

No Kubernetes, no cloud deployment, no infrastructure added for its own
sake — the system is designed to run locally first.

## Future media-generation options

Deliberately left open — see
[`docs/media-provider-strategy.md`](docs/media-provider-strategy.md) and
[ADR-004](docs/decisions/ADR-004-media-generation-abstraction.md) /
[ADR-005](docs/decisions/ADR-005-video-rendering.md) /
[ADR-006](docs/decisions/ADR-006-provider-selection.md). Candidates under
consideration (none selected, none referenced in code):
FFmpeg-based motion graphics, image+voice→MP4, Remotion, LTX, Hedra, other
image/video generation APIs, local/open-source models, 3D/avatar-based
generation, or a hybrid approach.

## Current status

**Implemented in Phase 0 (this commit):**
- Full repository structure, documentation, and ADRs.
- Real Pydantic domain models for all 17 core entities +
  provenance/source-span models ([`core/models/`](core/models/),
  [`core/provenance/`](core/provenance/)).
- Real abstract interfaces for every pipeline stage
  ([`core/interfaces/`](core/interfaces/),
  [`rendering/interfaces/`](rendering/interfaces/)) — zero concrete
  providers referenced anywhere.
- A FastAPI app with all 12 contracted routes declared using those
  domain models — **every route except `/health` returns HTTP 501.**
- Real file-upload validation/filename-sanitization helpers
  ([`backend/app/security/`](backend/app/security/)).
- A minimal, non-functional Next.js scaffold for the future dashboard.

**Not implemented yet (honest — do not assume otherwise):**
- No document parsing, OCR, or fact extraction runs.
- No script generation, translation, or verification runs.
- No storyboard planning, media generation, or video composition runs.
- No dashboard UI beyond a placeholder page.
- No database tables/persistence layer (SQLite engine is wired but no
  tables exist yet).
- No GitHub Issues were created as part of this task — see
  [`docs/TODO.md`](docs/TODO.md) for the phased backlog instead.

## Local development

No Docker required — everything runs natively.

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=.. uvicorn app.main:app --reload
```

Visit `http://localhost:8000/docs` for the OpenAPI UI, or:

```bash
curl http://localhost:8000/health
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Visit `http://localhost:3000` — currently a single placeholder page.

### Tests

```bash
cd backend && source .venv/bin/activate
PYTHONPATH=".:.." pytest ../tests
```

### One-shot setup

```bash
./scripts/setup_dev.sh
```

`docker-compose.yml` exists only as an **optional future convenience** —
it is not required and, since no Dockerfiles exist yet, does not
currently work.

## Repository structure

```
vaanireach/
├── docs/                 architecture, workflow, data model, API contract, ADRs
├── frontend/              Next.js scaffold (not functional yet)
├── backend/               FastAPI app — routes are 501 stubs
├── agents/                per-stage agent packages (namespaces only, no logic yet)
├── core/                  domain models, interfaces, provenance, workflow helpers
├── providers/             provider adapter placeholders (llm/translation/tts/visual/video/storage/mcp)
├── rendering/             video composition interfaces + adapter placeholder
├── tests/                 model + route-stub tests
├── scripts/                setup_dev.sh, check_env.py
└── sample_data/           synthetic sample document for future ingestion testing
```

## Security

- API keys live only in environment variables (`.env`, from
  `.env.example` — never committed).
- Uploaded files are validated by type and size and given sanitized,
  collision-resistant storage paths.
- Provider timeouts and rate limiting are documented design requirements
  (see [`core/security.py`](core/security.py)) for Phase 1+ — not yet
  enforced since no provider calls exist yet.
- Human approval is a hard requirement before publication — no code path
  in the design allows automatic publishing.

## Team

**non technical**
