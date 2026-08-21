# VaaniReach — Multilingual Outreach Video Generator

**Team:** non technical

> **Current status: a real, end-to-end web app.** Upload or paste a
> government notice at `frontend/` (Next.js), watch the `backend/`
> (FastAPI) pipeline extract facts, draft + verify narration in up to 9
> Indian languages, and render a talking-avatar + B-roll video with
> burned-in captions — then approve, reject, edit, or regenerate each
> language before anything is published. The Streamlit dashboard
> (`dashboard/`) is still in the repo as the original prototype UI, but
> the Next.js frontend + FastAPI backend described below are the real,
> working product now.

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
for paraphrases and eligibility statements), and finally a video is
composed — with **human approval required before anything is
published.** Anything that can't be verified is flagged, not invented.

## What it looks like

- A landing page that explains the product and leads straight into
  upload — hero section, capability stats, a "how it works" walkthrough,
  then the upload form.
- Drag-and-drop (or click-to-browse) upload for `.txt`/`.pdf` notices, or
  paste the notice text directly.
- Pick any of 9 Indian languages, a narrator voice, a narration style
  (news / storytelling), and pace/pitch — before generating.
- Live progress while the pipeline runs (facts as they're extracted,
  current pipeline stage).
- A review page per job: watch each language reach `pending_review`, see
  the extracted facts and verification results, preview the generated
  video (talking avatar in a circular picture-in-picture over B-roll
  footage, with burned-in captions), and Approve / Reject / Edit /
  Regenerate before it counts as published.

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
> Provider adapters handle the actual media generation/rendering, with
> multi-tier fallback so the pipeline degrades to something free and local
> rather than failing outright when a vendor key is missing or exhausted.

## Core workflow

1. **Document Intelligence** — take a pasted notice or a `.txt`/`.pdf`
   upload (including scanned PDFs via OCR).
2. **Source Fact Ledger** — structured, provenance-tagged facts (amounts,
   dates, deadlines, names, locations, eligibility, …) become the single
   source of truth. Gemini-backed extraction.
3. **Script Generation + Translation** — narration grounded in the
   ledger, generated per language for up to 9 Indian languages
   (`LanguageCode`: en, hi, mr, ta, bn, te, kn, ml, gu).
4. **Verification** — every generated claim is checked deterministically
   (regex/`rapidfuzz`) or semantically (Gemini) against the ledger;
   blocking unverified/contradicted claims trigger an automatic
   regenerate pass before anything reaches review.
5. **Media Generation + Composition** — a talking-avatar hook (Hedra →
   D-ID → local fallback), narrated B-roll images (Hugging Face), and
   voice audio (Sarvam AI → `edge-tts` fallback) are generated and
   composed via ffmpeg into a final MP4 with a circular avatar
   picture-in-picture and burned-in captions, plus SRT/VTT export.
6. **Human Review + Approval** — an officer reviews the script, the
   detected facts, and verification results per language on the review
   page, then Approves, Rejects, Edits (inline re-verification, no
   re-render cost), or Regenerates. **Publication is never automatic.**

## Technology stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3.12 + FastAPI + Pydantic v2 | Fast to build, strong typing, matches the domain-model-first approach — this is the real API the frontend talks to |
| Domain models | Plain Pydantic (`core/models/`) | Framework-agnostic — reused by providers, both frontends, tests |
| Frontend (`frontend/`) | Next.js (TypeScript, App Router) + Tailwind CSS + Framer Motion | The real product UI — landing page, drag-and-drop upload, live job progress, review/approval flow |
| Officer dashboard (`dashboard/`) | Streamlit | Original prototype UI, kept in the repo but superseded by the Next.js frontend above |
| Persistence | In-memory job store (backend) / Streamlit session state (dashboard) | Nothing survives a process restart yet — SQLite/SQLModel remain unimplemented |
| LLM | Google Gemini | Fact extraction, script generation, translation, semantic verification |
| B-roll images | Hugging Face Serverless Inference API | Free tier, no billing required |
| TTS | Sarvam AI → `edge-tts` fallback | Horizontal key rotation, then a free local fallback |
| Avatar / video composition | Hedra → D-ID → local fallback; ffmpeg-based renderer | 3-tier resilience; circular PiP + burned-in captions composited in one ffmpeg pass |
| Local dev | Native Python venv + npm | Runs directly on macOS/Linux/Windows, no Docker required |

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
| B-roll images | Hugging Face Serverless Inference API (free, no billing) | Content-addressed local cache → cold-start retry → local placeholder card |
| Video composition | ffmpeg-based renderer (`rendering/adapters/ffmpeg_video_renderer.py`) | N/A — local, no external API |

B-roll/avatar images were originally Google Imagen 3 — swapped to
Hugging Face once it turned out Imagen requires a billing-enabled Google
Cloud project even on free-tier Gemini API keys (every `generate_images`
call 404'd, confirmed live). `GeminiImagenProvider` is kept in the
codebase, just no longer wired in — see ADR-004.

Every fallback tier exists so the pipeline degrades to *something free
and local* instead of crashing when a vendor key is missing, rate-limited,
or exhausted — see each provider's module docstring
(`providers/llm/gemini_client.py`, `providers/tts/sarvam_tts_provider.py`,
`providers/video/avatar_provider.py`, `providers/visual/huggingface_provider.py`)
for the exact tier order.

## Current status

**Implemented and runnable today:**
- A working **Next.js frontend** (`frontend/`) — landing page, drag-and-drop
  upload, language/voice/style/pace/pitch pickers, live pipeline progress,
  and a per-job review page with Approve/Reject/Edit/Regenerate.
- A working **FastAPI backend** (`backend/app/routes/pipeline.py`) — job
  creation, status polling, video/caption serving, and the full
  approve/reject/edit/regenerate action set, all wired to the real
  pipeline (not stubs).
- Real Pydantic domain models for all core entities +
  provenance/source-span models ([`core/models/`](core/models/),
  [`core/provenance/`](core/provenance/)).
- **Fact extraction, multilingual script generation, and verification**
  — Gemini-backed, with deterministic (regex/`rapidfuzz`) + semantic
  verification and an automatic regenerate-on-failure loop
  ([`providers/llm/`](providers/llm/)).
- **Text-to-speech** — Sarvam AI with an `edge-tts` fallback, plus
  hook/body audio slicing for the avatar + B-roll split
  ([`providers/tts/`](providers/tts/)).
- **Talking-avatar generation** — Hedra → D-ID → local static fallback,
  3-tier resilience ([`providers/video/`](providers/video/)).
- **B-roll image generation** — Hugging Face's free Inference API with a
  content-addressed local cache and a local placeholder fallback
  ([`providers/visual/`](providers/visual/)).
- **Video composition** — ffmpeg: avatar hook + B-roll + circular avatar
  picture-in-picture with a gold ring border + burned-in captions → MP4 +
  SRT/VTT ([`rendering/adapters/`](rendering/adapters/)).
- The original **Streamlit Officer Review Dashboard**
  ([`dashboard/app.py`](dashboard/app.py)) still runs the same pipeline
  in-process as an alternate UI — kept for reference, not the primary
  product surface anymore.
- Real file-upload validation/filename-sanitization helpers
  ([`backend/app/security/`](backend/app/security/)), exercised by the
  live upload path.

**Not implemented yet (honest — do not assume otherwise):**
- **No `SceneDirector`/`SceneRenderer`** — nothing chooses a `SceneType`
  per fact or dispatches through `SceneRendererRegistry`; scene
  construction is hand-built. See ADR-004.
- **No `WorkflowEngine` or `agents/*` logic** — the backend sequences the
  pipeline itself rather than emitting `WorkflowEvent`s for a generic
  execution-trace layer.
- No database tables/persistence layer — jobs live in an in-memory store;
  nothing survives a backend restart.
- No GitHub Issues were created as part of this task — see
  [`docs/TODO.md`](docs/TODO.md) for the phased backlog instead (note:
  written for an earlier phase, and now stale relative to this README).

## Local development

No Docker required — everything runs natively.

### Backend (the real pipeline API)

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp ../.env.example ../.env       # fill in GEMINI_API_KEYS at minimum
PYTHONPATH=.. uvicorn app.main:app --reload
```

`GEMINI_API_KEYS` is the one hard requirement (fact extraction/script
generation have no fallback). `SARVAM_API_KEYS`/`HEDRA_API_KEYS`/
`DID_API_KEYS`/`HF_API_KEY` are all optional — every one of those
providers degrades to a free local fallback if unset.

Visit `http://localhost:8000/docs` for the OpenAPI UI, or:

```bash
curl http://localhost:8000/health
```

### Frontend (the real product UI)

```bash
cd frontend
npm install
npm run dev
```

Visit `http://localhost:3000` for the landing page → upload → review
flow. Set `NEXT_PUBLIC_API_BASE_URL` if the backend isn't on
`http://localhost:8000`.

### Dashboard (original prototype, still functional)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
streamlit run dashboard/app.py
```

See [`dashboard/README.md`](dashboard/README.md).

### Tests

```bash
cd backend && source .venv/bin/activate
PYTHONPATH=".:.." pytest ../tests
```

Includes real functional coverage, not just model/stub sanity checks —
e.g. [`tests/test_compose_pip_and_captions.py`](tests/test_compose_pip_and_captions.py)
composites a real PiP + caption track onto B-roll footage and asserts
actual pixel colors in the rendered frame (avatar circle, gold ring,
B-roll bleed-through at the corners) — not just "ffmpeg exited 0".

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
├── frontend/              Next.js app — the real product UI (landing page, upload, review)
├── backend/               FastAPI app — the real pipeline API
├── dashboard/             Streamlit Officer Review Dashboard — original prototype UI
├── agents/                per-stage agent packages (namespaces only, no logic yet)
├── core/                  domain models, interfaces, provenance, workflow helpers
├── providers/             LLM (Gemini), TTS (Sarvam/edge-tts), avatar (Hedra/D-ID),
│                          visual (Hugging Face) provider implementations
├── rendering/             video composition interfaces + ffmpeg adapter
├── fallback_assets/       the Tier-3 static avatar placeholder clip
├── local_cache/           generated B-roll image cache (gitignored, created at runtime)
├── tests/                 model + route + renderer tests
├── scripts/                setup_dev.sh, check_env.py
└── sample_data/           synthetic sample document for future ingestion testing
```

## Security

- API keys live only in environment variables (`.env`, from
  `.env.example` — never committed).
- Uploaded files are validated by type and size and given sanitized,
  collision-resistant storage paths ([`backend/app/security/`](backend/app/security/)).
- Provider calls (Gemini/Sarvam/Hedra/D-ID) implement their own
  timeout/retry/key-rotation resilience — see each provider's module
  docstring under `providers/`.
- Human approval is a hard requirement before publication — no code path
  in the design allows automatic publishing.

## Team

**non technical**
