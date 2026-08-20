# VaaniReach — Architecture

> **Status: Phases 0-5 built.** The Document Intelligence → Fact Ledger →
> Script Generation → Verification → TTS → Avatar → B-Roll → Video
> Composition pipeline runs end-to-end, driven in-process by the
> Streamlit Officer Review Dashboard (`dashboard/app.py`) — see
> [`README.md`](../README.md) for exactly what's implemented vs. still a
> stub (the FastAPI `backend/` routes and the `agents/`/orchestrator
> layer remain unimplemented; the dashboard bypasses both).

## Problem

Government and institutional announcements are often text-heavy and
English-only, so they reach only a fraction of their intended audience.
VaaniReach turns an official document into a short, multilingual,
narrated video — while never inventing or distorting the facts it
contains. Anything that can't be verified against the source must be
flagged, not fabricated.

## Pipeline

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

The verify → regenerate loop (I → F) is central: a script or translation
that fails verification is regenerated and re-verified, not published as-is.

## Layers

### Document Intelligence Layer
Ingests PDF/DOCX/image sources (OCR where needed) and produces a
`document → page → section/paragraph → source span` hierarchy. Every
downstream fact must be traceable back to a specific span here. See
[`core/interfaces/document_parser.py`](../core/interfaces/document_parser.py)
and [ADR-001](decisions/ADR-001-document-processing.md).

### Source Fact Ledger
The single source of truth for the whole system. Structured `SourceFact`
records (Person, Organization, Scheme, Location, Date, Deadline, Amount,
Percentage, Phone Number, URL, Eligibility, Requirement, Statistic,
Policy, Event) each carry provenance (`SourceSpan`) and a `criticality`.
Scripts, translations, and claims are generated *from* this ledger and
verified *against* it — never the reverse. See
[`core/models/fact.py`](../core/models/fact.py) and
[ADR-002](decisions/ADR-002-fact-ledger.md).

### Script Generation & Multilingual Layer
`ScriptGenerator` produces narration grounded in the Source Fact Ledger
for a given language/audience/duration; `TranslationProvider` fans that
narration out to every `Project.target_languages` entry.
`LanguageCode`/`LanguageConfig`-style abstractions mean the system is not
hardcoded to 3 languages — three (Hindi, Marathi, Tamil, say) is just the
initial demo set. See
[`core/interfaces/script_generator.py`](../core/interfaces/script_generator.py),
[`core/interfaces/translation_provider.py`](../core/interfaces/translation_provider.py).

### Verification Engine
First-class component, not an afterthought. `verify_deterministic` handles
objectively checkable `FactType`s (numbers, dates, amounts, percentages,
names, URLs, phone numbers, locations, scheme names); `verify_semantic`
handles paraphrases, translated claims, eligibility statements, and other
descriptive claims. Every claim resolves to `VERIFIED / CONTRADICTED /
NOT_FOUND / UNCERTAIN`; a `CRITICAL`-criticality claim that isn't VERIFIED
must block publication (`VerificationResult.is_blocking`). See
[`core/interfaces/verification_engine.py`](../core/interfaces/verification_engine.py).

### Provenance
`ProvenanceLink` joins a generated claim to its source fact, source span,
and current verification status — this is what lets a future dashboard
answer "where did this generated statement come from?" See
[`core/provenance/models.py`](../core/provenance/models.py) and
[`data-model.md`](data-model.md).

### Agentic Orchestration
Agents (`agents/*`) exist only where a real decision or tool selection is
needed — not agents-for-agents-sake. The intended design has a
`WorkflowEngine` sequence them:

```
extract facts → detect critical facts → generate script → verify
    → if failed: regenerate → verify again → proceed
```

and emit `WorkflowEvent`s for an execution-trace dashboard, with event
messages as concise, operational, user-facing strings — **never raw
model chain-of-thought**. See
[`core/interfaces/orchestrator.py`](../core/interfaces/orchestrator.py) and
[ADR-003](decisions/ADR-003-agent-orchestration.md).

**Current reality:** no `WorkflowEngine` or `agents/*` logic is
implemented yet. `dashboard/app.py` runs exactly this loop (verify →
regenerate-once-on-blocking-failure → proceed) by calling
`GeminiLLMProvider` directly and holding state in `st.session_state`
instead of emitting `WorkflowEvent`s. It is a faithful stand-in for the
loop's *behavior*, not for the orchestration/event-trace *architecture*
described above — that gap is still open.

### The Visual Strategy Layer (Scene Director → Scene Renderer → Provider)

> **"VaaniReach is not coupled to a particular video-generation
> technology. The AI Scene Director decides how information should be
> visually communicated, while provider adapters handle the actual media
> generation/rendering."**

Three independently swappable layers:

1. **`SceneDirector`** decides *what representation* fits a fact/claim
   (`SceneType`: `AI_VIDEO, IMAGE_MOTION, INFOGRAPHIC, MAP, AVATAR,
   THREE_D, TEXT, MIXED`) — e.g. an amount → an animated number, a
   location → a map, a deadline → a calendar animation, a statistic → a
   chart. Pure decision logic, no rendering.
2. **`SceneRenderer`** (the Visual Strategy layer) — one implementation
   per `SceneType`, dispatched via a `SceneRendererRegistry`. Turns a
   `Scene` into a `MediaAsset` without the rest of the system knowing how.
3. **Provider layer** (`providers/visual`, `providers/video`,
   `rendering/adapters`) — where a concrete `SceneRenderer` would
   eventually call a `VisualProvider` / `VideoGenerationProvider` /
   `VideoRenderer`. Providers are now selected and implemented (Gemini
   Imagen 3, Hedra/D-ID) — see
   [ADR-004](decisions/ADR-004-media-generation-abstraction.md),
   [ADR-005](decisions/ADR-005-video-rendering.md),
   [ADR-006](decisions/ADR-006-provider-selection.md). **`SceneDirector`
   and `SceneRenderer` themselves remain unimplemented** — nothing calls
   `choose_scene_type`/`plan_storyboard`, and `SceneRendererRegistry`
   still resolves zero renderers. `dashboard/app.py` calls the concrete
   providers directly, constructing `Scene` objects by hand, as a
   documented hackathon-scope shortcut around this layer.

See [`core/interfaces/scene_director.py`](../core/interfaces/scene_director.py)
and [`core/interfaces/scene_renderer.py`](../core/interfaces/scene_renderer.py).

### Video Composition
`VideoRenderer` takes scenes + audio + captions + visual assets +
transitions + timing + branding and produces MP4/SRT/VTT. Implemented on
MoviePy v2 — [`MoviePyVideoRenderer`](../rendering/adapters/moviepy_video_renderer.py),
see [ADR-005](decisions/ADR-005-video-rendering.md). See
[`rendering/interfaces/video_renderer.py`](../rendering/interfaces/video_renderer.py)
for the abstract contract.

### Human Review Dashboard
Built as a Streamlit app — [`dashboard/app.py`](../dashboard/app.py) (the
`frontend/` Next.js route referenced here originally remains an
unbuilt placeholder; the Streamlit dashboard is the dashboard that
actually runs). An officer reviews the Source (pasted notice text, the
Fact Ledger), Generated Content (per-language scripts, the final video),
and Verification (verified/contradicted/unverified claims with
explanations), then takes a Final Action: **Approve & Render** (produces
a preview video, does not publish anything) or **Regenerate** a
language's script. `REJECT`/`EDIT` from the original design aren't built.
**Publication is never automatic** — nothing in the dashboard uploads or
publishes the rendered video anywhere; it only offers it for local
download.

## Non-goals for this hackathon

- No Kubernetes, no cloud deployment — the system runs locally.
- No Docker requirement — native Python venv + npm is the primary path
  (`docker-compose.yml` is an optional convenience only).
- No multi-tenant auth system — a single placeholder dev identity is used.
- No `SceneDirector`/`SceneRenderer` or `WorkflowEngine`/`agents/*`
  implementation — see the sections above for what stands in for them.
