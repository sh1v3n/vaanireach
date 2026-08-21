"""TemplateStoryDirector — the guaranteed, deterministic StoryDirector
implementation (Phase 2 media plan, narrative layer). Zero external
dependencies, always works. Groups verified facts by narrative role and
writes short role-appropriate narration directly around them — it never
paraphrases facts into a flat paragraph and chops that up afterward,
which is the mechanism that produces a 1:1 fact->slide result.

No Gemini-enhanced variant exists yet (deliberately out of scope for
this phase — see
docs/superpowers/specs/2026-08-20-narrative-story-director-design.md).
`generator_name` is set to "template-v1" so a future enhanced generator
is a drop-in swap, not a rewrite.

Fact-invention guard: every word of narration this class writes is
either static connective phrasing (no digits, no proper nouns) or a
verbatim `SourceFact.value`. It never synthesizes a number, date, name,
location, organization, scheme detail, eligibility requirement,
deadline, URL, or phone number that isn't already in the facts it was
given — and never a relative temporal claim ("today", "soon", "time is
running out") that isn't grounded in the ledger, since this class has no
verified-current-date fact to compare against.

Revision 2 (post-review): HOOK and CONTEXT no longer exist as separate
scenes — a HOOK/CONTEXT split over the same fact bucket produced two
scenes that just repeated each other, so they're one scene now. URGENCY
is only synthesized when there's a second, distinct deadline-related
fact to add — one deadline fact alone produces exactly one DEADLINE
scene, not a DEADLINE scene and an URGENCY scene restating it. Scene
count therefore follows the facts actually present, not a fixed target.
"""
from __future__ import annotations

from collections import defaultdict

from core.interfaces.story_director import StoryDirector
from core.models.enums import FactType, NarrativeRole, SceneType, TransitionType
from core.models.fact import SourceFact
from core.models.narrative import NarrativeArc, VisualConcept
from core.models.storyboard import Scene

# Measured from Step B's Edge TTS smoke test (English neural voice):
# "This scheme provides a subsidy of five thousand rupees." (10 words) -> 4.25s.
WORDS_PER_SECOND = 2.3
MIN_SCENE_DURATION_SECONDS = 1.5
TARGET_DURATION_MIN_SECONDS = 20.0
TARGET_DURATION_MAX_SECONDS = 30.0

# Which narrative role a fact type's content serves — see the design
# spec's FactType -> NarrativeRole table. CONTEXT facts (ORGANIZATION,
# LOCATION) are consumed by the merged HOOK scene, not by a separate
# per-role loop iteration — see plan_narrative_arc.
_FACT_TYPE_TO_ROLE: dict[FactType, NarrativeRole] = {
    FactType.ORGANIZATION: NarrativeRole.CONTEXT,
    FactType.LOCATION: NarrativeRole.CONTEXT,
    FactType.SCHEME: NarrativeRole.ANNOUNCEMENT,
    FactType.POLICY: NarrativeRole.ANNOUNCEMENT,
    FactType.AMOUNT: NarrativeRole.BENEFIT,
    FactType.PERCENTAGE: NarrativeRole.BENEFIT,
    FactType.STATISTIC: NarrativeRole.BENEFIT,
    FactType.ELIGIBILITY: NarrativeRole.ELIGIBILITY,
    FactType.REQUIREMENT: NarrativeRole.HOW_TO,
    FactType.URL: NarrativeRole.HOW_TO,
    FactType.PHONE_NUMBER: NarrativeRole.HOW_TO,
    FactType.DEADLINE: NarrativeRole.DEADLINE,
    FactType.DATE: NarrativeRole.DEADLINE,
}
# PERSON, EVENT: no deterministic role in template-v1 — dropped, not
# guessed, if present (never crashes, never invents a role for them).

# Fact-derived core of the story, in story order. CONTEXT is deliberately
# absent here — its facts are consumed by the merged HOOK scene, not a
# separate scene (see the HOOK/CONTEXT merge below). PROBLEM only
# appears if a fact ever maps here (none do, yet).
_CORE_STORY_ORDER: list[NarrativeRole] = [
    NarrativeRole.PROBLEM,
    NarrativeRole.ANNOUNCEMENT,
    NarrativeRole.BENEFIT,
    NarrativeRole.ELIGIBILITY,
    NarrativeRole.HOW_TO,
    NarrativeRole.DEADLINE,
]

# A scene's initial scene_type, using the exact FactType -> SceneType
# table already approved for SceneDirector in the Phase 2 media plan.
# Scene.scene_type is a required field with no sensible "unset" value,
# and SceneDirector isn't wired into the pipeline yet (Phase 0), so this
# gives every scene a real, non-arbitrary starting value that a later
# SceneDirector.choose_scene_type() call can confirm or override —
# Scene fields are mutable, this doesn't lock anything in.
_FACT_TYPE_TO_SCENE_TYPE: dict[FactType, SceneType] = {
    FactType.AMOUNT: SceneType.INFOGRAPHIC,
    FactType.PERCENTAGE: SceneType.INFOGRAPHIC,
    FactType.STATISTIC: SceneType.INFOGRAPHIC,
    FactType.DEADLINE: SceneType.INFOGRAPHIC,
    FactType.DATE: SceneType.INFOGRAPHIC,
    FactType.LOCATION: SceneType.MAP,
}

# Recurring visual identity per role — the same icon vocabulary reappears
# across scenes (farmer_icon: BENEFIT+ELIGIBILITY; scheme_banner:
# ANNOUNCEMENT+CLOSING) so the video reads as one continuous visual
# system, not unrelated slides.
_ROLE_VISUAL_ELEMENTS: dict[NarrativeRole, list[str]] = {
    NarrativeRole.HOOK: ["location_pin", "org_seal_icon", "community_silhouette"],
    NarrativeRole.ANNOUNCEMENT: ["megaphone_icon", "scheme_banner"],
    NarrativeRole.BENEFIT: ["farmer_icon", "govt_building_icon", "rupee_badge"],
    NarrativeRole.ELIGIBILITY: ["farmer_icon", "checklist_icon", "checkmark_icon"],
    NarrativeRole.HOW_TO: ["document_icon", "csc_building_icon", "phone_icon"],
    NarrativeRole.DEADLINE: ["calendar_icon", "countdown_marker"],
    NarrativeRole.URGENCY: ["calendar_icon", "clock_icon", "pulse_ring"],
    NarrativeRole.CTA: ["megaphone_icon", "arrow_icon", "url_badge"],
    NarrativeRole.CLOSING: ["scheme_banner", "org_seal_icon"],
}

# What happens ON SCREEN, in order — action descriptions, never a
# restatement of the scene's narration text (review requirement).
_ROLE_VISUAL_BEATS: dict[NarrativeRole, list[str]] = {
    NarrativeRole.HOOK: [
        "Establish the district as the setting",
        "Introduce the issuing office as the messenger",
        "Draw the viewer's attention toward what follows",
    ],
    NarrativeRole.ANNOUNCEMENT: [
        "Reveal the scheme name on screen",
        "Unfurl a banner-style announcement graphic",
        "Transition from setting to subject",
    ],
    NarrativeRole.BENEFIT: [
        "Establish a farmer in an agricultural setting",
        "Introduce government assistance arriving",
        "Animate the benefit amount toward the farmer",
    ],
    NarrativeRole.ELIGIBILITY: [
        "Show the farmer beside a simple checklist",
        "Highlight the qualifying criteria one by one",
        "Confirm eligibility with a checkmark",
    ],
    NarrativeRole.HOW_TO: [
        "Trace the application journey from farmer to office",
        "Show the application point as a physical waypoint",
        "Surface the contact details for reaching it",
    ],
    NarrativeRole.DEADLINE: [
        "Reveal a calendar",
        "Mark the closing date on the calendar",
        "Hold on the date for emphasis",
    ],
    NarrativeRole.URGENCY: [
        "Return to the calendar from the DEADLINE scene",
        "Emphasize the gap between now and the marked date",
    ],
    NarrativeRole.CTA: [
        "Bring back the application journey visual",
        "Overlay the closing date as a final reminder",
        "End on a clear call to action",
    ],
    NarrativeRole.CLOSING: [
        "Return to the scheme banner",
        "Pair it with the issuing authority's seal",
        "Settle on a calm closing frame",
    ],
}

# Escalating emphasis toward the CTA/deadline: calm fades early, brisk
# cuts through the informational middle, more energetic slide/zoom late.
_ROLE_TRANSITION_OUT: dict[NarrativeRole, TransitionType] = {
    NarrativeRole.HOOK: TransitionType.FADE,
    NarrativeRole.PROBLEM: TransitionType.CUT,
    NarrativeRole.ANNOUNCEMENT: TransitionType.CUT,
    NarrativeRole.BENEFIT: TransitionType.CUT,
    NarrativeRole.ELIGIBILITY: TransitionType.CUT,
    NarrativeRole.HOW_TO: TransitionType.SLIDE,
    NarrativeRole.DEADLINE: TransitionType.ZOOM,
    NarrativeRole.URGENCY: TransitionType.SLIDE,
    NarrativeRole.CTA: TransitionType.FADE,
}


def estimate_narration_duration(narration: str) -> float:
    """Public so other narration-producing code (e.g.
    dynamic_narration.py's LLM-rewritten lines) can derive a scene's
    pre-TTS duration estimate the same way this module's own templates
    do — the real per-language duration is always overwritten later by
    the actual TTS audio length; this is only ever a placeholder."""
    word_count = len(narration.split())
    return max(MIN_SCENE_DURATION_SECONDS, word_count / WORDS_PER_SECOND)


def _dedupe_contained(values: list[str]) -> list[str]:
    """Drops any value that is already a substring of another value in
    the list — e.g. an ORGANIZATION fact whose value already embeds a
    LOCATION fact's value ("Office of the District Collectorate,
    Riverbend District" contains "Riverbend District"). Without this,
    joining both produces "...Riverbend District and Riverbend
    District.", a redundancy that undercuts narration quality without
    changing which facts are cited (the dropped value's text is still
    present verbatim, just not restated)."""
    kept = []
    for i, value in enumerate(values):
        if any(i != j and value in other for j, other in enumerate(values)):
            continue
        kept.append(value)
    return kept


def _join_values(facts: list[SourceFact]) -> str:
    values = _dedupe_contained([f.value for f in facts])
    if len(values) == 1:
        return values[0]
    return ", ".join(values[:-1]) + " and " + values[-1]


# Per-role narration when a role bucket has exactly one fact, or every
# fact in it lacks a distinguishing qualifier — the original phrasing
# this feature always used, centralized here so both the joined path
# and the per-fact fallback below share one definition.
_JOINED_NARRATION: dict[NarrativeRole, str] = {
    NarrativeRole.ANNOUNCEMENT: "Announcing {value}.",
    NarrativeRole.BENEFIT: "Eligible recipients receive {value}.",
    NarrativeRole.ELIGIBILITY: "This scheme is open to {value}.",
    NarrativeRole.HOW_TO: "To apply, use {value}.",
    NarrativeRole.DEADLINE: "Applications close on {value}.",
}

# Per-role narration for ONE fact that carries a qualifier — used when a
# role bucket has multiple same-typed facts that must stay
# distinguishable (tabular/list source data, e.g. several closing dates
# for different applicant categories) instead of being joined into one
# run-on sentence that loses which value belongs to what.
_QUALIFIED_NARRATION: dict[NarrativeRole, str] = {
    NarrativeRole.ANNOUNCEMENT: "For {qualifier}, announcing {value}.",
    NarrativeRole.BENEFIT: "For {qualifier}, eligible recipients receive {value}.",
    NarrativeRole.ELIGIBILITY: "For {qualifier}, this scheme is open to {value}.",
    NarrativeRole.HOW_TO: "For {qualifier}, {value}.",
    NarrativeRole.DEADLINE: "For {qualifier}, the deadline is {value}.",
}


def _per_fact_narration(role: NarrativeRole, fact: SourceFact) -> str:
    """One fact's own sentence — used when a role bucket is split into
    one scene per fact (see plan_narrative_arc). Falls back to the
    generic single-fact phrasing when THIS particular fact has no
    qualifier even though a sibling fact in the same bucket does."""
    if fact.qualifier:
        return _QUALIFIED_NARRATION[role].format(qualifier=fact.qualifier, value=fact.value)
    return _JOINED_NARRATION[role].format(value=fact.value)


def _infer_scene_type(facts: list[SourceFact]) -> SceneType:
    for fact in facts:
        scene_type = _FACT_TYPE_TO_SCENE_TYPE.get(fact.fact_type)
        if scene_type is not None:
            return scene_type
    return SceneType.TEXT


def _visual_concept(role: NarrativeRole, narration: str) -> VisualConcept:
    return VisualConcept(
        summary=narration,
        elements=_ROLE_VISUAL_ELEMENTS[role],
        visual_beats=_ROLE_VISUAL_BEATS[role],
    )


class TemplateStoryDirector(StoryDirector):
    def plan_narrative_arc(self, facts: list[SourceFact]) -> tuple[NarrativeArc, list[Scene]]:
        if not facts:
            raise ValueError("plan_narrative_arc: facts is empty — nothing to build a story from")

        project_id = facts[0].project_id
        document_id = facts[0].document_id

        buckets: dict[NarrativeRole, list[SourceFact]] = defaultdict(list)
        for fact in facts:
            role = _FACT_TYPE_TO_ROLE.get(fact.fact_type)
            if role is not None:
                buckets[role].append(fact)

        storyboard_id = f"arc-{document_id}"
        scenes: list[Scene] = []

        def add_scene(role: NarrativeRole, cited: list[SourceFact], narration: str) -> Scene:
            scene = Scene(
                storyboard_id=storyboard_id,
                order_index=len(scenes),
                scene_type=_infer_scene_type(cited),
                narrative_role=role,
                narration_segment_text=narration,
                source_fact_ids=[f.id for f in cited],
                duration_seconds=estimate_narration_duration(narration),
                visual_concept=_visual_concept(role, narration),
            )
            scenes.append(scene)
            return scene

        # --- HOOK: merged with what used to be a separate CONTEXT scene.
        # A HOOK scene and a CONTEXT scene both drawing on the same
        # ORGANIZATION/LOCATION facts repeated each other; one scene,
        # citing both, opens the story instead. Direct address ("An
        # announcement from/concerning X —") reads more dynamically than
        # a flat dateline sentence, WITHOUT assuming a specific audience
        # (e.g. "Farmers of X") that isn't grounded in any extracted
        # fact — a tax/health/education notice has no farmers in it, and
        # the fact-invention guard applies to the audience claim just as
        # much as to any other narration text.
        context_facts = buckets.get(NarrativeRole.CONTEXT, [])
        if context_facts:
            location = next((f for f in context_facts if f.fact_type == FactType.LOCATION), None)
            anchor = location or context_facts[0]
            if anchor.fact_type == FactType.LOCATION:
                hook_narration = f"An announcement concerning {anchor.value} — this concerns you."
            else:
                hook_narration = f"An announcement from {anchor.value} — this concerns you."
            add_scene(NarrativeRole.HOOK, context_facts, hook_narration)

        # --- fact-derived core, in story order ---
        for role in _CORE_STORY_ORDER:
            role_facts = buckets.get(role, [])
            if not role_facts:
                continue  # e.g. PROBLEM: no fact type maps here in template-v1, so it's skipped, not invented
            if len(role_facts) > 1 and any(f.qualifier for f in role_facts):
                # Tabular/list source data: several same-typed facts that
                # must stay distinguishable (e.g. multiple closing dates
                # for different applicant categories, extracted with a
                # qualifier for exactly this reason) — one scene per
                # fact, in document order, instead of joining them into
                # one run-on sentence that loses which value belongs to
                # what (e.g. three dates read back to back with no
                # indication of which is which).
                for fact in role_facts:
                    add_scene(role, [fact], _per_fact_narration(role, fact))
            else:
                joined = _join_values(role_facts)
                narration = _JOINED_NARRATION[role].format(value=joined)
                add_scene(role, role_facts, narration)

        # --- URGENCY: only if there's a SECOND, distinct deadline-related
        # fact to add (e.g. an extended/revised date) AND those deadlines
        # weren't already split into their own distinguishable scenes
        # above — a qualifier-driven split already voices each deadline
        # clearly, so restating them jointly here would be pure
        # repetition. "Time is running out" without a verified
        # current-date comparison is also exactly the unsupported
        # relative-temporal claim the fact-consistency rule forbids.
        deadline_facts = buckets.get(NarrativeRole.DEADLINE, [])
        deadline_facts_were_split = len(deadline_facts) > 1 and any(f.qualifier for f in deadline_facts)
        if len(deadline_facts) > 1 and not deadline_facts_were_split:
            joined = _join_values(deadline_facts)
            add_scene(NarrativeRole.URGENCY, deadline_facts, f"Note: the deadline is {joined}.")

        # announcement_facts is kept (not folded into a CLOSING scene — see
        # docs/superpowers/specs/2026-08-20-video-captions-avatar-shortening-design.md) because
        # `title` below still needs it.
        announcement_facts = buckets.get(NarrativeRole.ANNOUNCEMENT, [])

        # --- transitions: every scene but the last points to a TransitionType ---
        for scene in scenes[:-1]:
            scene.transition_to_next_scene = _ROLE_TRANSITION_OUT.get(scene.narrative_role, TransitionType.CUT)
        scenes[-1].transition_to_next_scene = None

        title = announcement_facts[0].value if announcement_facts else "Untitled Scheme"
        role_sequence = " -> ".join(s.narrative_role.value for s in scenes)
        # Scene counts in practice stay small; cover the vowel-sound
        # numbers ("eight", "eleven", "eighteen") that need "An" rather
        # than building a general number-to-words article rule.
        article = "An" if len(scenes) in (8, 11, 18) else "A"
        story_summary = f"{article} {len(scenes)}-scene story for {title}: {role_sequence}."
        target_duration = sum(s.duration_seconds for s in scenes)

        arc = NarrativeArc(
            project_id=project_id,
            document_id=document_id,
            title=title,
            story_summary=story_summary,
            target_duration_seconds=target_duration,
            scene_ids=[s.id for s in scenes],
            generator_name="template-v1",
        )
        return arc, scenes
