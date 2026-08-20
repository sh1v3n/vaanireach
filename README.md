# VaaniReach — Multilingual Outreach Video Generator

**Team:** non technical

> **Current status: end-to-end pipeline running via the Streamlit
> dashboard.** Paste an English notice into `dashboard/app.py` and it
> extracts facts, drafts + verifies scripts in 4 Indian languages, and
> renders a talking-avatar + B-roll video with captions — see
> [Current Status](#current-status) below for exactly what's real and
> what's still a stub. The FastAPI `backend/` app is a separate,
> **not-yet-wired-up** front-end: every route on it except `GET /health`
> still returns HTTP 501, because the dashboard talks to the provider
> layer in-process and never calls it.

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

The video-generation technology was **deliberately left undecided at
first** (see [ADR-004](docs/decisions/ADR-004-media-generation-abstraction.md)),
then chosen once the interfaces existed — see
[Media Generation](#media-generation) below.

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
[`docs/decisions/`](docs/decisions/) (ADRs, including the media-provider
decisions).

### Key architectural principle

> VaaniReach is not coupled to a particular video-generation technology.
> The AI Scene Director decides how information should be visually
> communicated, while provider adapters handle the actual media
> generation/rendering.

## Core workflow

1. **Document Intelligence** — ingest PDF/DOCX/image sources, extract
   page-level text/headings/tables with page-and-span provenance.
   *(⚠️ plain-text only today — see [Current Status](#current-status).)*
2. **Source Fact Ledger** — structured, provenance-tagged facts (amounts,
   dates, deadlines, names, locations, eligibility, …) become the single
   source of truth. ✅ implemented (Gemini-backed extraction).
3. **Script Generation + Translation** — narration grounded in the
   ledger, generated for at least 3 Indian languages (extensible to more).
   ✅ implemented (4 languages ship in the dashboard).
4. **Verification** — every generated claim is checked
   deterministically or semantically against the ledger; critical
   unverified/contradicted claims block publication. ✅ implemented.
5. **Storyboard / Scene Planning** — a Scene Director chooses *what*
   visual representation fits each fact (map, infographic, animated
   number, …), independent of *which* vendor renders it. *(⚠️ not
   implemented — the dashboard builds a fixed avatar-hook +
   3-image-B-roll storyboard by hand.)*
6. **Media Generation + Composition** — visual/audio assets are produced
   and composed into a final video with captions. ✅ implemented
   (Pollinations.ai + Hedra/D-ID + Sarvam/edge-tts + MoviePy).
7. **Human Review + Approval** — an officer reviews source, generated
   content, and verification results, then Approves / Rejects /
   Regenerates / Edits. **Publication is never automatic.** ✅ Approve +
   Regenerate implemented in the dashboard; Reject/Edit are not.

## Technology stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3.12 + FastAPI + Pydantic v2 | Fast to build, strong typing, matches the domain-model-first approach — **still 501 stubs, not used by the dashboard** |
| Domain models | Plain Pydantic (`core/models/`) | Framework-agnostic — reused by providers, the dashboard, tests |
| Officer dashboard | Streamlit (`dashboard/app.py`) | The actual working front-end — in-process, no HTTP hop |
| Frontend (`frontend/`) | Next.js (TypeScript, App Router) | Scaffolded for a future web dashboard; **not the one that runs today** |
| Persistence | None yet | Everything lives in `st.session_state` for the dashboard session; SQLite/SQLModel remain unimplemented |
| LLM | Google Gemini (`gemini-3.6-flash`) | See [Media Generation](#media-generation) |
| B-roll images | Pollinations.ai (free, keyless) | See [Media Generation](#media-generation) |
| TTS | Sarvam AI → `edge-tts` fallback | See [Media Generation](#media-generation) |
| Avatar / video composition | Hedra → D-ID → local fallback; MoviePy v2 | See [Media Generation](#media-generation) |
| Local dev | Native Python venv | Runs directly on macOS/Linux/Windows, no Docker required |

No Kubernetes, no cloud deployment, no infrastructure added for its own
sake — the system is designed to run locally first.

## Media generation

Originally left open — see
[`docs/media-provider-strategy.md`](docs/media-provider-strategy.md) and
[ADR-004](docs/decisions/ADR-004-media-generation-abstraction.md) /
[ADR-005](docs/decisions/ADR-005-video-rendering.md) /
[ADR-006](docs/decisions/ADR-006-provider-selection.md) — then decided:

| Concern | Provider(s) | Resilience shape |
|---|---|---|
| LLM (facts / scripts / translation / semantic verification) | Google Gemini | Horizontal API-key rotation |
| TTS | Sarvam AI → `edge-tts` | Horizontal rotation, then a free local fallback |
| Talking-avatar hook | Hedra → D-ID → static local clip | 2 vendors, then a locally-generated placeholder |
| B-roll images | Pollinations.ai (free, keyless REST API) | Content-addressed local cache → retry with backoff → local placeholder card |
| Video composition | MoviePy v2 (bundles its own ffmpeg) | N/A — local, no external API |

B-roll/avatar images went through two prior providers before landing on
Pollinations.ai, both dropped for the same reason: Google Imagen 3 needs
a billing-enabled Google Cloud project even on free-tier Gemini keys
(every `generate_images` call 404'd, confirmed live), and Hugging Face's
free Inference API (and Together AI, one of the backends its router can
pick) started gating image generation behind billing/deposit too.
`GeminiImagenProvider` and `HuggingFaceVisualProvider` are both kept in
the codebase, just no longer wired in — see ADR-004's two revision notes.

Every fallback tier exists so the dashboard degrades to *something free
and local* instead of crashing when a vendor key is missing, rate-limited,
or exhausted — see each provider's module docstring
(`providers/llm/gemini_client.py`, `providers/tts/sarvam_tts_provider.py`,
`providers/video/avatar_provider.py`, `providers/visual/pollinations_visual_provider.py`)
for the exact tier order.

**Known gap:** no concrete `SceneDirector`/`SceneRenderer` exists yet —
`dashboard/app.py` calls these providers directly rather than through
that layer. See ADR-004.

## Current status

**Implemented and runnable today:**
- Full repository structure, documentation, and ADRs (Phase 0).
- Real Pydantic domain models for all 17 core entities +
  provenance/source-span models ([`core/models/`](core/models/),
  [`core/provenance/`](core/provenance/)).
- Real abstract interfaces for every pipeline stage
  ([`core/interfaces/`](core/interfaces/),
  [`rendering/interfaces/`](rendering/interfaces/)).
- **Fact extraction, multilingual script generation, and verification**
  — Gemini-backed, with deterministic (regex/`rapidfuzz`) + semantic
  verification and an automatic regenerate-on-failure loop
  ([`providers/llm/`](providers/llm/)).
- **Text-to-speech** — Sarvam AI with an `edge-tts` fallback, plus
  hook/body audio slicing for the avatar + B-roll split
  ([`providers/tts/`](providers/tts/)).
- **Talking-avatar generation** — Hedra → D-ID → local static fallback,
  3-tier resilience ([`providers/video/`](providers/video/)).
- **B-roll image generation** — Pollinations.ai's free, keyless REST API
  with a content-addressed local cache and a local placeholder fallback
  ([`providers/visual/`](providers/visual/)).
- **Video composition** — MoviePy v2: avatar hook + Ken Burns B-roll +
  audio overlay + burned-in captions → MP4 + SRT
  ([`rendering/adapters/`](rendering/adapters/)).
- **Officer Review Dashboard** — a working Streamlit app
  ([`dashboard/app.py`](dashboard/app.py)) that runs the whole pipeline
  above in-process: paste a notice → review the Fact Ledger and
  per-language verified scripts → Approve & Render → download the MP4 +
  SRT.
- Real file-upload validation/filename-sanitization helpers
  ([`backend/app/security/`](backend/app/security/)) — not yet exercised
  by any live upload path.

**Not implemented yet (honest — do not assume otherwise):**
- **The FastAPI `backend/` app is unchanged since Phase 0** — every route
  except `/health` still returns HTTP 501. The dashboard does not call it.
- **No document parsing beyond plain text** — no PDF/DOCX/image ingestion
  or OCR; the dashboard takes pasted text or a `.txt` upload only.
- **No `SceneDirector`/`SceneRenderer`** — nothing chooses a `SceneType`
  per fact or dispatches through `SceneRendererRegistry`; the dashboard
  calls the visual/avatar/rendering providers directly. See ADR-004.
- **No `WorkflowEngine` or `agents/*` logic** — the dashboard sequences
  the pipeline itself and holds state in `st.session_state` rather than
  emitting `WorkflowEvent`s for an execution-trace dashboard.
- No database tables/persistence layer — everything lives in the
  Streamlit session; nothing survives a dashboard restart.
- The Next.js `frontend/` is still the Phase 0 placeholder — it is not
  the dashboard described above.
- No GitHub Issues were created as part of this task — see
  [`docs/TODO.md`](docs/TODO.md) for the phased backlog instead.

## Local development

No Docker required — everything runs natively.

### Dashboard (the actual working pipeline)

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # fill in GEMINI_API_KEYS at minimum — see dashboard/README.md
streamlit run dashboard/app.py
```

`GEMINI_API_KEYS` is the one hard requirement (fact extraction/script
generation have no fallback). `SARVAM_API_KEYS`/`HEDRA_API_KEYS`/
`DID_API_KEYS` are all optional — every one of those providers degrades
to a free local fallback if unset. See [`dashboard/README.md`](dashboard/README.md).

### Backend

Independent of the dashboard above — every route except `/health` is
still a Phase 0 HTTP 501 stub (see [Current Status](#current-status)).

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

Includes real functional coverage now, not just model/stub sanity checks
— e.g. [`tests/test_phase4_renderer_smoke.py`](tests/test_phase4_renderer_smoke.py)
feeds a dummy avatar clip + 3 dummy images + a dummy audio track through
`MoviePyVideoRenderer` (both its direct and generic-ABC entry points) and
asserts a real, playable MP4 comes out with no codec errors.

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
├── dashboard/             Streamlit Officer Review Dashboard — the working pipeline UI
├── frontend/              Next.js scaffold (not functional yet)
├── backend/               FastAPI app — routes are 501 stubs
├── agents/                per-stage agent packages (namespaces only, no logic yet)
├── core/                  domain models, interfaces, provenance, workflow helpers
├── providers/             LLM (Gemini), TTS (Sarvam/edge-tts), avatar (Hedra/D-ID),
│                          visual (Pollinations.ai) provider implementations
├── rendering/             video composition interfaces + MoviePy adapter
├── fallback_assets/       the Tier-3 static avatar placeholder clip
├── local_cache/           generated B-roll image cache (gitignored, created at runtime)
├── tests/                 model + route-stub + renderer smoke tests
├── scripts/                setup_dev.sh, check_env.py
└── sample_data/           synthetic sample document for future ingestion testing
```

## Security

- API keys live only in environment variables (`.env`, from
  `.env.example` — never committed).
- Uploaded files are validated by type and size and given sanitized,
  collision-resistant storage paths (backend upload path only — not yet
  exercised by the dashboard, which takes text directly).
- Provider calls (Gemini/Sarvam/Hedra/D-ID) implement their own
  timeout/retry/key-rotation resilience — see each provider's module
  docstring under `providers/`. The `core/security.py`
  constants remain documentation-as-defaults, not yet centrally enforced.
- Human approval is a hard requirement before publication — no code path
  in the design, or in the dashboard, allows automatic publishing.

## Team

**non technical**
