# Review-and-Approve Frontend + thin job API

**Status:** DRAFT — awaiting user review before implementation planning.
**Scope:** a new frontend (`frontend/`, currently a bare Next.js scaffold) and a new backend job layer
(`backend/app/routes/`, currently 11 unrelated 501-stub routes for a heavier, unbuilt multi-stage
architecture — untouched, this design adds new routes alongside them, does not implement them). The
actual generation pipeline (`rendering/multilingual_video.py`, `providers/narrative/*`,
`providers/video/avatar_provider.py`, `rendering/adapters/*`) is **out of scope and gets zero
behavioral changes** — this design only adds three new fields to one existing dataclass and otherwise
composes the pipeline's existing public functions from new orchestration code.

## Context

The template pipeline (`run_full_pipeline` → `generate_language_video`) is finished, tested, and
live-verified: document text in, a `LanguageVideoResult` per requested language out (real fact
extraction, dynamic per-scene narration, multilingual translation, TTS, 3-tier avatar lip-sync,
burned-in captions, fact verification). `dashboard/local_demo.py` (Streamlit) already proves the
whole thing end-to-end.

The hackathon problem statement (PS-02) requires more than "generate and show a video," though.
Checked against it directly:

**Compulsory, already satisfied by the existing pipeline (no changes needed here):**
- Source document understanding (fact extraction)
- Multilingual generation, 3+ languages (9 supported)
- Multimodal video generation (narration + visuals + captions)
- Fact verification against the source, with blocking-issue detection

**Compulsory, NOT satisfied by anything built so far:** *"The final content must be reviewable and
approved by a human before publication."* Neither `local_demo.py` nor the frontend design approved
earlier in this session has any review/approval gate — both just display finished videos. This design
exists to close that gap.

**Bonus features this design also happens to unlock cheaply** (data already computed by the pipeline,
just never exposed in any UI):
- Subtitle export (SRT/VTT) — `LanguageVideoResult.srt_text`/`.vtt_text` already exist.
- Fact-level highlighting (where a fact came from in the source document) —
  `SourceFact.source_span` already carries `page_number` + `text_span`.

Voice personalization and MCP media tools are explicitly out of scope for this design (real net-new
pipeline work, lower priority than closing the compulsory gap).

## Decisions (confirmed with user)

- **Backend contract**: a new, small job API (`backend/app/routes/pipeline.py`), not an
  implementation of the existing 11-route 501-stub contract designed for a much heavier, persisted
  multi-stage architecture that predates the real pipeline and doesn't match how it actually runs
  (one synchronous call, not independently-resumable stages).
- **Job store**: in-memory only (a plain dict keyed by job id). No database. Job history is lost on
  backend restart — acceptable for a single-operator demo tool, explicitly not "fixed" here.
- **Job execution**: the pipeline call runs in a background thread (it's synchronous and makes real
  blocking HTTP calls) so the FastAPI event loop is never blocked; the HTTP response returns
  immediately with a job id.
- **Review gate**: a job's status moves `pending` → `running` → **`pending_review`** (not `done`) once
  generation finishes. `pending_review` is the terminal state of *generation*; publication is a
  separate, explicit human action.
- **Per-language actions, once a job is `pending_review`**: **Approve**, **Reject**, **Regenerate**,
  **Edit** — all four, scoped as follows:
  - *Approve* — flips that language's status to `published`. Pure status change, no pipeline call.
  - *Reject* — flips that language's status to `rejected`. Pure status change, no pipeline call.
  - *Edit* — the officer edits one scene's narration text inline. On save, the edited text is
    immediately re-verified via `DeterministicFactVerifier.verify_claim` (direct call, no LLM, no
    TTS, no render — cheap) and the result (verified / flagged, with the reason) is shown before the
    officer decides whether to keep it. The edited text replaces that scene's
    `narration_segment_text` in the job's stored scene list for that language. Editing alone does
    **not** touch the rendered video — the officer must Regenerate afterward for an edit to reach the
    actual video/audio.
  - *Regenerate* — re-invokes `generate_language_video(..., precomputed_scenes=<that language's
    current, possibly-edited scenes>)`, reusing the same `facts` and `image_paths` already computed
    for the job (stored on the job record from the original run). This re-runs only the per-language
    tail (translate-if-non-English → TTS → avatar → captions → compose) — it does **not** re-extract
    facts, re-call the narration LLM, or re-render B-roll images, so it costs nothing on the stages
    that didn't change. Only actionable while that language is already `pending_review` (same
    precondition as Approve/Reject); status stays `pending_review` throughout — see the endpoint
    description below for the transient `regenerating` flag and failure handling.
- **What the officer sees on the review screen, per language** (all compulsory-Review-Dashboard
  requirements, satisfied from data the pipeline already produces):
  - The script — each scene's narration text (already the real, spoken-in-that-language text, since
    scenes are post-translation).
  - Detected facts — the full `SourceFact` list extracted from the source document (shared across
    all languages, shown once per job or once per language, same data).
  - Verification results — per-claim status from `DeterministicFactVerifier`, with any blocking issue
    clearly flagged (not just the aggregate `verified_count`/`blocking_count` already on
    `LanguageVideoResult` — the actual per-scene detail).
  - The rendered video itself, with its avatar-tier badge (real lip-sync / static placeholder /
    degraded) so the officer knows what they're approving.
- **Data model change (the only pipeline-adjacent change in this whole design)**: add three fields to
  `LanguageVideoResult` (`rendering/multilingual_video.py`):
  - `facts: list[SourceFact]`
  - `verification_results: list[VerificationResult]`
  - `image_paths: list[str]`

  All three values already exist inside `generate_language_video` — it receives `facts` and
  `image_paths` as parameters, and already computes `results = DeterministicFactVerifier().verify_batch(claims, facts)`
  — this only stores them on the object it already returns. Purely additive: new fields, no existing
  field changes shape or meaning, no existing caller (`local_demo.py`, every test) breaks.
  `image_paths` is needed because Regenerate (below) must call `generate_language_video` directly
  with the *same* B-roll images the original run produced, without re-rendering them — and nothing
  currently exposes that list to a caller of `run_full_pipeline`.
- **Visual theme**: dark navy/charcoal base, gold/mustard accent, formal serif wordmark — matches the
  government-portal reference image the user supplied. Tailwind CSS for layout/styling, Framer Motion
  for scroll-reveal and micro-interaction animation (both confirmed with user; GSAP considered and
  rejected as more setup time than this scope needs).
- **Pages**:
  1. **Home** (`/`) — hero (navy background, gold-accented headline, upload/paste form as a light
     card on the dark hero) + language picker + Generate button, then a "how it works" 3-step section
     below the fold.
  2. **Job page** (`/jobs/[jobId]`) — polls status while `pending`/`running`; once `pending_review`,
     renders one review card per language with everything listed above and the four action buttons;
     languages that reach `published`/`rejected` show their final state instead of the action buttons.

## Backend design

### New module: `backend/app/routes/pipeline.py`

Endpoints (all new; the existing 11 stub routes in `backend/app/routes/` are untouched):

- `POST /pipeline/jobs` — multipart: `file` (optional) or `text` (optional, at least one required) +
  `languages` (list of `LanguageCode` values). Validates at least one of file/text is present and at
  least one language is selected (mirrors `local_demo.py`'s existing validation). Extracts document
  text via the shared text-extraction module (see below), creates a job record with a new UUID (this
  UUID doubles as `project_id` throughout — no separate id is invented), starts a background thread
  calling `run_full_pipeline(text, languages=languages, project_id=job_id)` **once, for every
  requested language together, exactly as `local_demo.py` already does** — this call is atomic: it
  either produces a `LanguageVideoResult` for every requested language, or (if extraction finds zero
  facts, or any language's translation/TTS step raises outside the avatar step's own internal
  degrade-to-placeholder safety net) raises, and the *whole job* fails — no partial per-language
  results from the initial generation. This exactly matches `run_full_pipeline`'s existing, tested,
  all-or-nothing behavior; this design adds no new partial-failure handling at the initial-generation
  layer, keeping the "zero pipeline behavior change" promise. On success, the background thread
  stores each returned `LanguageVideoResult` (now carrying `facts`/`image_paths`/
  `verification_results`) into the job record and sets job status to `pending_review`. On failure, it
  sets job status to `failed` and stores the exception message as `error`. The HTTP response itself
  returns immediately, before the thread finishes: `{"job_id": "...", "status": "pending"}` (HTTP
  202).

  Per-language isolation only exists from Regenerate onward (see below) — once a job reaches
  `pending_review`, each language's subsequent Approve/Reject/Edit/Regenerate is independent of the
  others.
- `GET /pipeline/jobs/{job_id}` — returns the job's current state:
  ```json
  {
    "job_id": "...",
    "status": "pending | running | pending_review | failed",
    "error": "string | null",
    "languages": {
      "hi": {
        "status": "pending_review | published | rejected",
        "regenerating": false,
        "avatar_tier": 1,
        "avatar_composited": true,
        "video_url": "/pipeline/jobs/{job_id}/video/hi",
        "srt_url": "/pipeline/jobs/{job_id}/captions/hi.srt",
        "vtt_url": "/pipeline/jobs/{job_id}/captions/hi.vtt",
        "scenes": [
          {"order_index": 0, "narrative_role": "hook", "narration_segment_text": "...", "source_fact_ids": ["..."]},
          "..."
        ],
        "verified_count": 5,
        "blocking_count": 0,
        "verification_results": [
          {"claim_id": "...", "status": "verified", "is_blocking": false, "explanation": "..."},
          "..."
        ]
      }
    },
    "facts": [
      {"id": "...", "fact_type": "amount", "value": "₹10,000", "raw_text": "...", "source_span": {"page_number": 1, "text_span": "..."}},
      "..."
    ]
  }
  ```
  `facts` is included once (shared across all languages, per `run_full_pipeline`'s own
  language-independence principle) rather than duplicated per language.
- `GET /pipeline/jobs/{job_id}/video/{language}` — serves the language's current video file (whatever
  the most recent Approve/Reject/Regenerate cycle produced) directly, via `FileResponse`.
- `GET /pipeline/jobs/{job_id}/captions/{language}.{srt,vtt}` — serves the stored caption text as a
  plain-text/subtitle response (the bonus "Subtitle Export" feature — the data already exists on
  `LanguageVideoResult`, this just exposes it).
- `POST /pipeline/jobs/{job_id}/languages/{language}/approve` — sets that language's status to
  `published`. 409 if the job isn't `pending_review` or that language isn't currently
  `pending_review`.
- `POST /pipeline/jobs/{job_id}/languages/{language}/reject` — sets that language's status to
  `rejected`. Same preconditions as approve.
- `POST /pipeline/jobs/{job_id}/languages/{language}/edit` — body: `{"scene_order_index": 2, "narration_segment_text": "edited text"}`.
  Builds a `Claim` with `claim_text=<edited text>` and `source_fact_ids=<that scene's existing,
  unchanged source_fact_ids>` (the officer edits phrasing, not which facts a scene cites), then calls
  `DeterministicFactVerifier().verify_claim(claim, job.facts)`. Note this matches the verifier's
  actual, already-tested semantics exactly: the cited-id check only confirms those ids exist in the
  ledger, while the content checks (digits/URLs/phone numbers/proper nouns) are validated against the
  *entire* job fact ledger, not just this scene's cited subset — identical to how verification already
  behaves everywhere else in the pipeline, nothing new introduced here. On success, updates the stored
  scene's narration text and returns the fresh verification result. Does not touch the rendered video.
- `POST /pipeline/jobs/{job_id}/languages/{language}/regenerate` — 409 under the same precondition as
  approve/reject (that language must currently be `pending_review`) and additionally if
  `regenerating` is already `true` for it (no overlapping regenerate calls for the same language). Re-invokes
  `generate_language_video(facts, image_paths, story_director=TemplateStoryDirector(),
  translator=GroqTranslationProvider(), target_language=language, project_id=job_id,
  precomputed_scenes=<stored scenes for that language>)` in a background thread, reusing the job's
  stored `facts`/`image_paths` (read from any of that job's existing `LanguageVideoResult`s — identical
  across languages) so fact extraction, narration drafting, and B-roll rendering never run again.
  `story_director`/`translator` are required parameters on `generate_language_video` with no default,
  so Regenerate constructs plain default instances, matching what `run_full_pipeline` uses internally.
  While regenerating, that language's status stays `pending_review` (not a new "regenerating" state —
  the frontend can distinguish it via a per-language `regenerating: bool` flag on the job response if
  needed) with its previous video still servable until the new one replaces it. If this call raises,
  the language's stored result and status are left untouched (the previous, still-valid result stays
  in place) and the error is returned directly as the HTTP response to this call, not persisted onto
  the job record — a failed Regenerate is a transient action failure, not a new job-level or
  language-level terminal state.

### Job record shape (in-memory, `dict[str, JobRecord]`)

```python
@dataclass
class LanguageJobState:
    status: Literal["pending_review", "published", "rejected"]
    regenerating: bool  # True only while a Regenerate call for this language is in flight
    result: LanguageVideoResult  # includes the new facts/verification_results/image_paths fields

@dataclass
class JobRecord:
    job_id: str  # also used as project_id everywhere this job calls the pipeline
    status: Literal["pending", "running", "pending_review", "failed"]
    error: str | None
    languages: dict[LanguageCode, LanguageJobState]  # empty until status == "pending_review"
```

`facts`/`image_paths` are not stored redundantly at the job level — they're read off any one
language's `result` (identical across all languages in a job, per `run_full_pipeline`'s
language-independence principle) whenever Regenerate or the `GET` endpoint needs them. Job-level
`status` is simple because initial generation is atomic (see the `POST /pipeline/jobs` description
above): it is `pending` → `running` → either `pending_review` (with every requested language present
in `languages`) or `failed` (with `languages` empty and `error` set) — never a partial mix during
initial generation. After that point, each language's own `status` (`pending_review` / `published` /
`rejected`) moves independently via Approve/Reject/Regenerate — a job with one language `published`
and another still `pending_review` is a normal, expected state once generation itself has finished.

### Shared text-extraction module

`extract_text_from_upload` and `_ocr_pdf_bytes` move out of `dashboard/local_demo.py` into a new
`providers/documents/text_extraction.py` (pure functions, no Streamlit dependency — currently they
don't use any Streamlit API except the `st.info()` progress message in `local_demo.py`'s version,
which stays in `local_demo.py` as a thin wrapper calling the shared function). Both
`dashboard/local_demo.py` and `backend/app/routes/pipeline.py` import from this shared module — one
OCR implementation, not two copies that can drift.

### Threading model

`run_full_pipeline` and `generate_language_video` are synchronous and make real blocking HTTP calls
(Groq, Cloudflare, Sarvam, D-ID/Hedra) — calling them directly from an `async def` route handler would
block the event loop for the full multi-minute duration. Both the initial generation and Regenerate
run via `threading.Thread(target=..., daemon=True)`, writing results back into the shared
`JobRecord` dict under a lock (`threading.Lock` per job, to avoid a torn read if `GET
/pipeline/jobs/{id}` is polled mid-write).

## Frontend design

### Stack additions to the existing scaffold

- Tailwind CSS (`tailwindcss`, `postcss`, `autoprefixer`) — layout, spacing, color, responsive.
- Framer Motion (`framer-motion`) — scroll-reveal (`whileInView`), staggered list/card entrance,
  hover/tap micro-interactions.
- No new component library (e.g. no shadcn/MUI) — hand-built components on top of Tailwind utilities,
  consistent with keeping the dependency surface small for a hackathon timeline.

### Theme tokens (Tailwind config)

- `navy` (base dark background, e.g. `#0f1a2b`–`#1a2740` range for depth/cards-on-dark)
- `gold` (accent, e.g. `#c9a227`–`#d4af37` range — headlines, active states, badges)
- Serif display font for the wordmark/headlines (system serif stack or a Google Font, e.g. "Playfair
  Display" — self-hostable/CDN-free per the reference image's formal-serif "R republic" mark), sans
  font for body text.

### Pages

**`/` (Home)**
- Hero: navy background, gold-accented "VaaniReach" wordmark + one-line value proposition, subtle
  animated background texture (reusing the wavy-line motif from the reference image is optional
  polish, not required).
- Upload card: light card floating on the dark hero (file upload OR paste-text toggle, mirroring
  `local_demo.py`'s existing two input modes), language multiselect (checkboxes/pills for the 9
  supported languages), Generate button. Framer Motion entrance animation on load.
- Below the fold: "How it works" — Upload → Extract & Verify → Review & Approve — 3-step icon-card
  row, `whileInView` stagger-in as the user scrolls.
- Generate button submits to `POST /pipeline/jobs`, then routes to `/jobs/[jobId]`.

**`/jobs/[jobId]` (Job / Review page)**
- Same navy/gold header band for visual consistency with Home.
- While `status` is `pending`/`running`: a progress state (polling `GET /pipeline/jobs/{id}` every
  ~3s) — no fake progress bar (the real pipeline has no meaningful sub-step progress signal to poll),
  just a clear "generating your videos — this takes a few minutes" state with a subtle animated
  indicator.
- Once `pending_review` (or later): one review card per language —
  - Header: language name, avatar-tier badge (✅ real lip-sync / ⚠️ placeholder / ⚠️ degraded — same
    semantics `local_demo.py` already surfaces), current status badge
    (pending_review/published/rejected).
  - Video player.
  - Facts-verified / blocking-issues counts, with an expandable list of the actual flagged claims
    (not just the count) when `blocking_count > 0`.
  - Script panel: each scene's narration text, editable inline (Edit action) with an inline
    "re-verify" result shown after save.
  - Detected-facts panel: the source fact list (type, value, and — bonus feature — the
    `source_span.text_span` it came from), shown once per job (not duplicated per language card).
  - Subtitle download links (SRT/VTT — bonus feature).
  - Action buttons: Approve / Reject / Regenerate, visible only while that language is
    `pending_review`; once `published`/`rejected`, replaced with a static status indicator instead.

### API client / types

`frontend/src/lib/api-client.ts` and `frontend/src/types/index.ts` get new functions/types scoped to
this job API (`createJob`, `getJob`, `approveLanguage`, `rejectLanguage`, `editScene`,
`regenerateLanguage`) — the existing `notImplemented` stubs for the old heavy contract
(`createProject`, `listFacts`, etc.) are left as-is, untouched, since they belong to the separate
501-stub architecture this design doesn't implement.

## Testing

- **Backend**: new `backend/tests/test_pipeline_routes.py` using FastAPI's `TestClient` with
  `run_full_pipeline`/`generate_language_video` monkeypatched to fast fakes (no real network calls,
  no D-ID/Groq cost) — covers job creation, polling through pending → pending_review, approve/reject
  status transitions and their 409 preconditions, edit + re-verification, and regenerate reusing
  stored facts/image_paths (asserted via a call-count/argument check on the faked
  `generate_language_video`, confirming it receives `precomputed_scenes` and never re-triggers fact
  extraction or image rendering).
- **Shared text-extraction module**: existing OCR test coverage (`tests/test_local_demo` equivalent,
  currently informal/manual per this session's earlier live verification) gets a real pytest file
  once the module moves — reusing the same scanned-PDF synthesis approach already proven this
  session.
- **Frontend**: no new test framework introduced for this hackathon timeline; manual verification
  against a running backend is the acceptance method, consistent with `local_demo.py`'s own testing
  approach so far this session.
- **Pipeline regression guard**: after adding the two new `LanguageVideoResult` fields, re-run the
  full existing narrative/dynamic-narration/verification/multilingual-video test suites (as already
  done twice this session for prior changes) to confirm zero behavioral change to the pipeline
  itself.

## Explicitly out of scope (YAGNI)

- Authentication, multi-user support, job history persisting across backend restarts.
- Implementing any of the existing 11 heavy 501-stub routes (`documents`, `facts`, `scripts`,
  `storyboard`, `translate`, `verification`, `workflow`, `approval`, etc.) — they stay exactly as they
  are, untouched, dead code for a different architecture.
- Live re-render triggered automatically by Edit — Edit only updates stored text + shows verification
  feedback; reaching the actual video requires an explicit Regenerate.
- Voice personalization, MCP media tools (bonus features, real net-new pipeline work, not attempted
  here).
- Job cancellation.
