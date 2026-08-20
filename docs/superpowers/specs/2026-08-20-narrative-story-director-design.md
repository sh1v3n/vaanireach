# Narrative / Story Director — Design Spec

**Status:** Approved (with adjustments), not yet implemented.
**Scope:** Phase 2 media pipeline. Inserts a story layer above `SceneDirector`
so the guaranteed pipeline produces a coherent narrative video instead of
one infographic per fact.

## Problem

The guaranteed Phase 2 pipeline (approved separately) maps facts to scenes
1:1 via `SceneDirector`, which produces a sequence of unrelated slides:

```
Fact 1 -> infographic
Fact 2 -> infographic
Fact 3 -> infographic
```

The required output is a story-driven public information video (closer to
a government PSA / short explainer) with a beginning/middle/end structure:
context -> announcement -> benefit -> eligibility -> action -> urgency -> CTA.

## Pipeline shape

```
Verified Source Fact Ledger
    -> StoryDirector.plan_narrative_arc(facts)
    -> NarrativeArc (+ ordered Scenes, each with a narrative_role)
    -> SceneDirector.choose_scene_type per scene   (unchanged interface)
    -> SceneRenderer                                (unchanged interface)
    -> TTS
    -> FFmpeg
    -> Video
```

`StoryDirector` sits where "Script Generation" would have run. It does not
write one flat paragraph and chop it into scenes after the fact — chopping
after the fact is exactly what produces the 1:1 fact->slide pattern this
design exists to avoid. It writes narration per scene directly, in a voice
appropriate to that scene's narrative role.

The existing `Script` model is **not** replaced. Per adjustment #5, it
becomes a derived/compatibility view, assembled from the ordered scenes
when something needs it (`narration_text` = scene narrations joined in
order; `claim_ids`/`source_fact_ids` = the union across scenes). No
separate hand-authored script-generation step exists before the arc.

## Data model

### New enums — `core/models/enums.py`

```python
class NarrativeRole(str, Enum):
    HOOK = "hook"
    CONTEXT = "context"
    PROBLEM = "problem"
    ANNOUNCEMENT = "announcement"
    BENEFIT = "benefit"
    ELIGIBILITY = "eligibility"
    HOW_TO = "how_to"
    DEADLINE = "deadline"
    URGENCY = "urgency"
    CTA = "cta"
    CLOSING = "closing"


class TransitionType(str, Enum):
    CUT = "cut"
    FADE = "fade"
    SLIDE = "slide"
    ZOOM = "zoom"
    # Maps onto ffmpeg's xfade filter at Video Composition time
    # (fade / slideleft / zoomin, etc.) — no new dependency.
```

### Language independence

`NarrativeArc` and its scenes carry no `language` field. Structure —
roles, order, fact assignment, visual concepts, transitions — is decided
once from the (language-independent) Source Fact Ledger. Narration text
is written in the source language by `template_story_director.py`; each
target language's `narration_segment_text` is produced afterward by
running the existing `TranslationProvider` over each scene's narration,
not by re-running `StoryDirector`. This is what keeps Step F ("re-run for
Hindi/Marathi/Tamil, no pipeline changes") true for the story layer too —
translation still never touches structure, order, or fact grounding.

### New model — `core/models/narrative.py`

```python
class VisualConcept(VaaniBaseModel):
    summary: str
    """Human-readable one-liner, e.g. "farmer -> government assistance -> Rs.2,000 benefit"."""
    elements: list[str]
    """Ordered visual beats a renderer composes, e.g.
    ["farmer_icon", "govt_building_icon", "rupee_badge"]."""


class NarrativeArc(IdentifiedModel):
    project_id: str
    document_id: str
    title: str
    story_summary: str          # ADDED per adjustment #3
    target_duration_seconds: float
    scene_ids: list[str]        # ordered
    generator_name: str         # "template-v1" for this phase
    status: StoryboardStatus    # reuse existing enum
```

### `Scene` gets 2 new fields — `core/models/storyboard.py`

Everything else on `Scene` is unchanged (`storyboard_id`, `order_index`,
`scene_type`, `narration_segment_text`, `claim_ids`, `source_fact_ids`,
`duration_seconds`, `media_asset_ids`).

```python
narrative_role: NarrativeRole
visual_concept: VisualConcept | None = None   # NEW field, distinct from visual_prompt
transition_to_next_scene: TransitionType | None = None   # None on the last scene
```

**Correction from the in-chat draft:** `visual_prompt` (existing `str |
None` field) is left untouched rather than retyped — it's already used
as a plain prompt string by `providers/video/avatar_provider.py` and
`dashboard/app.py` (both existing Phase 3/4 code, out of scope for this
change). `visual_concept` is a new, separate field for the local
HTML/CSS renderer's structured input; the two fields serve different
renderers and can both be set independently.

### New interface — `core/interfaces/story_director.py`

```python
class StoryDirector(ABC):
    @abstractmethod
    def plan_narrative_arc(self, facts: list[SourceFact]) -> tuple[NarrativeArc, list[Scene]]:
        ...
```

Returns both, rather than only the arc plus scene ids to resolve later:
this codebase has no persistence/repository layer yet (per
`docs/architecture.md`, that's a Phase 1 concern), so an id-only return
would be unresolvable by any caller. `NarrativeArc.scene_ids` still holds
the ordered ids (for the model's own bookkeeping / future persistence),
and must match `[s.id for s in scenes]` exactly.

### Implementation location

`providers/narrative/template_story_director.py` — the guaranteed,
deterministic implementation (this phase's only implementation, per
adjustment #8). No Gemini-enhanced variant is built now.

## Non-negotiable constraints (adjustment #6)

`StoryDirector` may:
- reorder verified facts
- group related facts
- choose narrative roles
- generate concise narration *around* verified facts

`StoryDirector` must **not** introduce any number, date, name, location,
organization, scheme detail, eligibility requirement, deadline, URL, phone
number, or claim that is not present in the verified Source Fact Ledger.

## Fact traceability (adjustment #7)

Every scene retains `source_fact_ids` and `claim_ids` — including
connective/rhetorical scenes that don't introduce new information:

- `HOOK` cites the same fact IDs as the `CONTEXT`/`ANNOUNCEMENT` scenes it sets up.
- `URGENCY` cites the same fact ID as the `DEADLINE` scene it amplifies.
- `CTA`/`CLOSING` cite the `HOW_TO` and `DEADLINE` fact IDs they restate.

No scene is ever fact-id-empty. This is what lets Final Verification
(Step G of the approved media plan) re-check every scene's narration
against the Fact Ledger, including the connective ones.

## FactType -> NarrativeRole mapping (template-v1's deterministic logic)

| FactType | NarrativeRole |
|---|---|
| ORGANIZATION, LOCATION | CONTEXT |
| SCHEME, POLICY | ANNOUNCEMENT |
| AMOUNT, PERCENTAGE, STATISTIC | BENEFIT |
| ELIGIBILITY | ELIGIBILITY |
| REQUIREMENT, URL, PHONE_NUMBER | HOW_TO |
| DEADLINE, DATE | DEADLINE (+ synthesized URGENCY scene, reusing the same fact id) |

(Corrected from the in-chat draft: `REQUIREMENT` reads as a procedural
"what to do" fact — e.g. "register at your nearest Common Service
Centre" — not an eligibility criterion, so it groups with `HOW_TO`
alongside `URL`/`PHONE_NUMBER` rather than with `ELIGIBILITY`.)

`HOOK`, `CTA`, and `CLOSING` are synthesized scaffolding scenes — not
derived from a dedicated FactType — built from the fact IDs of the
CONTEXT/ANNOUNCEMENT and HOW_TO/DEADLINE scenes respectively, per the
traceability rule above.

## Out of scope for this phase

- Gemini-enhanced `StoryDirector` (adjustment #8) — the interface leaves
  room for it (`generator_name` field), not built now.
- Any change to `SceneDirector`, `SceneRenderer`, or the renderers built
  in Steps A-C.
- Step D (media composition) — explicitly deferred until after the
  generated `NarrativeArc` + scenes for the sample notice are reviewed
  (adjustment #11).

## Test requirements (adjustment #10, written before implementation)

A test must verify, for the sample notice (`sample_data/sample_scheme_notice.txt`):
1. Narrative roles are assigned to every scene.
2. Scenes have an ordered narrative flow (`order_index` strictly increasing).
3. Every scene has non-empty `source_fact_ids`.
4. Narration text only references facts present in the verified ledger
   (no invented numbers/dates/names/etc. — checked against the constraint
   list above).
5. `VisualConcept` exists for every scene.
6. Transitions are assigned between scenes (every scene but the last has
   a non-null `transition_to_next_scene`).
7. `NarrativeArc.story_summary` is present and non-empty.
8. Target duration is respected. At `StoryDirector` planning time, no
   audio exists yet, so `Scene.duration_seconds` here is a word-count/
   reading-speed *estimate*, not a final value — per the already-approved
   rule that audio duration is the source of truth for scene timing, Step
   B's `EdgeTtsProvider` overwrites each scene's real `duration_seconds`
   once its narration is actually synthesized. The test checks that
   `NarrativeArc.target_duration_seconds` falls in the 30-45s range and
   equals the sum of the scenes' *estimated* durations at planning time —
   not that it will match post-TTS reality exactly.

## Demonstration (adjustment #9 / #11)

Before touching Step D, run `template_story_director.py` against the
sample notice's extracted facts and print the full `NarrativeArc` plus
every `Scene` (role, narration, fact/claim ids, visual concept, transition)
for review — not just the test's pass/fail.
