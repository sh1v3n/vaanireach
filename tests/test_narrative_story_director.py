"""Narrative/Story Director — the properties required by
docs/superpowers/specs/2026-08-20-narrative-story-director-design.md
(adjustment #10) plus the review-round refinements (HOOK/CONTEXT merge,
URGENCY gated on a real signal, no unsupported relative-temporal
language, content-driven scene count, visual_beats), exercised against
sample_data/sample_scheme_notice.txt's facts.

1. Narrative roles are assigned to every scene.
2. Scenes have an ordered narrative flow (order_index strictly increasing).
3. Every scene has non-empty source_fact_ids.
4. Narration only references verified facts (no invented numbers/dates/
   names/etc.) — checked structurally: template-v1 is deterministic, so
   every fact-bearing scene's narration must contain the literal value of
   at least one fact it cites.
5. VisualConcept (summary/elements/visual_beats) exists for every scene.
6. Transitions are assigned between scenes (every scene but the last has
   a non-null transition_to_next_scene; the last has none).
7. NarrativeArc.story_summary is present and non-empty.
8. Target duration is respected: 20-30s range, and equals the sum of the
   scenes' (pre-TTS, estimated) durations.
9. No duplicate narrative roles (HOOK/CONTEXT redundancy fix).
10. No unsupported relative-temporal language (URGENCY/CTA fix).
11. Scene count follows content, not a fixed target.
"""
from __future__ import annotations

import re

import pytest

from core.interfaces.story_director import StoryDirector
from core.models.enums import Criticality, FactType, NarrativeRole
from core.models.fact import SourceFact
from core.models.narrative import NarrativeArc, VisualConcept
from core.models.storyboard import Scene
from core.provenance.models import SourceSpan
from providers.narrative.template_story_director import TemplateStoryDirector

PROJECT_ID = "proj-sample-notice"
DOCUMENT_ID = "doc-sample-notice"


def _fact(fact_type: FactType, value: str, raw_text: str, criticality: Criticality = Criticality.MEDIUM) -> SourceFact:
    return SourceFact(
        project_id=PROJECT_ID,
        document_id=DOCUMENT_ID,
        fact_type=fact_type,
        value=value,
        raw_text=raw_text,
        source_span=SourceSpan(document_id=DOCUMENT_ID, page_number=1, text_span=raw_text),
        criticality=criticality,
        confidence=0.95,
        extractor_name="manual-test-fixture",
    )


def sample_notice_facts() -> list[SourceFact]:
    """Hand-extracted from sample_data/sample_scheme_notice.txt — stands
    in for the (unimplemented) fact-extraction agent. Every value here is
    copied verbatim from that file. NOTE: each call produces fresh
    SourceFact ids (IdentifiedModel's default_factory) — tests must not
    call this a second time and expect ids to line up with an
    already-built arc; use the arc_and_scenes fixture's returned facts
    instead."""
    return [
        _fact(FactType.ORGANIZATION, "Office of the District Collectorate, Riverbend District",
              "OFFICE OF THE DISTRICT COLLECTORATE, RIVERBEND DISTRICT"),
        _fact(FactType.LOCATION, "Riverbend District", "Riverbend District"),
        _fact(FactType.SCHEME, "Kisan Sahayata Yojana (Farmer Support Scheme)",
              'Kisan Sahayata Yojana (Farmer Support Scheme)'),
        _fact(FactType.ELIGIBILITY,
              "farmers holding less than 2 hectares in Riverbend District",
              "All small and marginal farmers holding less than 2 hectares of agricultural land "
              "within Riverbend District are eligible to apply.",
              criticality=Criticality.HIGH),
        _fact(FactType.AMOUNT, "₹2,000", "₹2,000 (Rupees Two Thousand only)", criticality=Criticality.CRITICAL),
        _fact(FactType.DEADLINE, "31 March 2026", "31 March 2026", criticality=Criticality.CRITICAL),
        _fact(FactType.URL, "www.example-scheme.gov.in", "www.example-scheme.gov.in"),
        _fact(FactType.REQUIREMENT, "register at their nearest Common Service Centre",
              "Applicants may register at their nearest Common Service Centre or online"),
        _fact(FactType.PHONE_NUMBER, "1800-000-0000", "1800-000-0000"),
    ]


@pytest.fixture(scope="module")
def arc_and_scenes() -> tuple[NarrativeArc, list[Scene], list[SourceFact]]:
    """Returns the exact SourceFact instances used to build the arc —
    sample_notice_facts() assigns a fresh id on every call, so any test
    that needs to resolve a scene's source_fact_ids back to real facts
    must use these, not call sample_notice_facts() again."""
    director: StoryDirector = TemplateStoryDirector()
    facts = sample_notice_facts()
    arc, scenes = director.plan_narrative_arc(facts)
    assert arc.scene_ids == [s.id for s in scenes], "NarrativeArc.scene_ids must match the returned scenes, in order"
    return arc, scenes, facts


def test_narrative_roles_assigned_to_every_scene(arc_and_scenes):
    _, scenes, _ = arc_and_scenes
    assert len(scenes) > 0
    for scene in scenes:
        assert isinstance(scene.narrative_role, NarrativeRole)


def test_scenes_have_ordered_narrative_flow(arc_and_scenes):
    _, scenes, _ = arc_and_scenes
    order_indices = [s.order_index for s in scenes]
    assert order_indices == sorted(order_indices)
    assert order_indices == list(range(len(scenes))), "order_index must be strictly increasing from 0"


def test_every_scene_has_source_fact_ids(arc_and_scenes):
    _, scenes, _ = arc_and_scenes
    for scene in scenes:
        assert len(scene.source_fact_ids) > 0, f"scene {scene.narrative_role} has no source_fact_ids"


def test_narration_only_references_verified_facts(arc_and_scenes):
    """Structural grounding check: every scene's narration must contain
    the literal value of at least one fact it cites. template-v1 is a
    fixed template over real fact values, so this also catches the
    failure mode of a scene citing facts it doesn't actually narrate."""
    _, scenes, facts = arc_and_scenes
    facts_by_id = {f.id: f for f in facts}
    for scene in scenes:
        cited_values = [facts_by_id[fid].value for fid in scene.source_fact_ids if fid in facts_by_id]
        assert cited_values, f"scene {scene.narrative_role} cites no known facts"
        assert any(value in scene.narration_segment_text for value in cited_values), (
            f"scene {scene.narrative_role} narration does not literally contain any cited fact value: "
            f"{cited_values!r} not found in {scene.narration_segment_text!r}"
        )


def test_visual_concept_exists_for_every_scene(arc_and_scenes):
    _, scenes, _ = arc_and_scenes
    for scene in scenes:
        assert isinstance(scene.visual_concept, VisualConcept)
        assert scene.visual_concept.summary.strip() != ""
        assert len(scene.visual_concept.elements) > 0


def test_visual_beats_describe_action_not_narration(arc_and_scenes):
    """visual_beats says what happens on screen, in order — it must not
    just restate the narration text scene review feedback flagged."""
    _, scenes, _ = arc_and_scenes
    for scene in scenes:
        beats = scene.visual_concept.visual_beats
        assert len(beats) > 0, f"scene {scene.narrative_role} has no visual_beats"
        for beat in beats:
            assert beat.strip() != ""
            assert beat != scene.narration_segment_text, (
                f"scene {scene.narrative_role} visual_beat repeats narration verbatim: {beat!r}"
            )


def test_transitions_assigned_between_scenes(arc_and_scenes):
    _, scenes, _ = arc_and_scenes
    for scene in scenes[:-1]:
        assert scene.transition_to_next_scene is not None, f"scene {scene.narrative_role} is missing a transition"
    assert scenes[-1].transition_to_next_scene is None, "the last scene must not have an outgoing transition"


def test_no_duplicate_narrative_roles(arc_and_scenes):
    """Each narrative role should appear at most once — a HOOK scene and
    a CONTEXT scene both re-stating the same fact(s) is exactly the
    redundancy flagged in review; they should be one scene, not two."""
    _, scenes, _ = arc_and_scenes
    roles = [s.narrative_role for s in scenes]
    assert len(roles) == len(set(roles)), f"duplicate narrative roles produced: {roles!r}"


def test_opening_scene_covers_context_without_a_separate_context_scene(arc_and_scenes):
    """HOOK and CONTEXT are merged: the opening scene alone should ground
    both the location and organization facts; no separate CONTEXT-role
    scene should exist for this document."""
    _, scenes, facts = arc_and_scenes
    roles = {s.narrative_role for s in scenes}
    assert NarrativeRole.CONTEXT not in roles, "a separate CONTEXT scene should not exist alongside HOOK"
    hook_scenes = [s for s in scenes if s.narrative_role == NarrativeRole.HOOK]
    assert len(hook_scenes) == 1
    hook = hook_scenes[0]
    facts_by_id = {f.id: f for f in facts}
    cited_types = {facts_by_id[fid].fact_type for fid in hook.source_fact_ids if fid in facts_by_id}
    assert FactType.LOCATION in cited_types
    assert FactType.ORGANIZATION in cited_types


def test_urgency_omitted_when_it_would_only_repeat_the_deadline(arc_and_scenes):
    """This document has exactly one DEADLINE fact and no independent
    urgency signal (countdown, extension notice, etc.) — URGENCY must be
    omitted rather than restate the DEADLINE scene under a different
    label."""
    _, scenes, _ = arc_and_scenes
    roles = {s.narrative_role for s in scenes}
    assert NarrativeRole.URGENCY not in roles


_BANNED_RELATIVE_TEMPORAL_PHRASES = [
    "today", "tomorrow", "tonight", "soon", "this week", "this month",
    "right now", "currently", "shortly", "hurry", "running out", "act now",
    "don't wait", "time is short", "time is running out",
]


def test_no_unsupported_relative_temporal_language(arc_and_scenes):
    """Relative temporal claims are only valid if grounded in a verified
    current-date comparison fact, which this ledger doesn't have. Every
    deadline reference must use the DEADLINE fact's own absolute date."""
    _, scenes, _ = arc_and_scenes
    for scene in scenes:
        lowered = scene.narration_segment_text.lower()
        for phrase in _BANNED_RELATIVE_TEMPORAL_PHRASES:
            assert phrase not in lowered, (
                f"scene {scene.narrative_role} narration contains unsupported relative-temporal "
                f"phrase {phrase!r}: {scene.narration_segment_text!r}"
            )


def test_scene_count_is_not_hardcoded(arc_and_scenes):
    """Regression guard for the fix itself: merging HOOK+CONTEXT and
    omitting URGENCY should produce fewer than the original 10 scenes
    for this document — count follows content, not a fixed target."""
    _, scenes, _ = arc_and_scenes
    assert len(scenes) < 10


def test_cta_and_closing_scenes_are_never_produced(arc_and_scenes):
    """Regression guard: CTA and CLOSING are pure restatements of facts
    already spoken earlier (CTA repeats HOW_TO's URL + DEADLINE's date;
    CLOSING repeats ANNOUNCEMENT's scheme name + HOOK's org) — dropped
    entirely so the video can fit a 20-30s target without losing any
    fact. See docs/superpowers/specs/2026-08-20-video-captions-avatar-shortening-design.md."""
    _, scenes, _ = arc_and_scenes
    roles = {s.narrative_role for s in scenes}
    assert NarrativeRole.CTA not in roles
    assert NarrativeRole.CLOSING not in roles


def test_narrative_arc_has_story_summary(arc_and_scenes):
    arc, _, _ = arc_and_scenes
    assert arc.story_summary.strip() != ""


def test_target_duration_respected(arc_and_scenes):
    arc, scenes, _ = arc_and_scenes
    assert 20.0 <= arc.target_duration_seconds <= 30.0, (
        f"target_duration_seconds={arc.target_duration_seconds} outside the 20-30s range"
    )
    scene_sum = sum(s.duration_seconds for s in scenes)
    assert scene_sum == pytest.approx(arc.target_duration_seconds, abs=0.01)


def test_story_director_rejects_facts_it_did_not_receive():
    """Fact-invention guard: the arc must never cite a fact id that
    wasn't in the input list."""
    director = TemplateStoryDirector()
    facts = sample_notice_facts()
    _, scenes = director.plan_narrative_arc(facts)
    valid_ids = {f.id for f in facts}
    for scene in scenes:
        for fid in scene.source_fact_ids:
            assert fid in valid_ids, f"scene cites unknown fact id {fid!r} not present in the input Fact Ledger"


def test_narration_contains_no_digits_absent_from_facts():
    """A cheap but real invented-number guard: every run of digits that
    appears in any scene's narration must appear in the raw_text/value of
    at least one fact in the ledger (guards against e.g. a hallucinated
    amount or date creeping into template narration)."""
    facts = sample_notice_facts()
    known_digit_runs = set()
    for f in facts:
        known_digit_runs.update(re.findall(r"\d+", f.value))
        known_digit_runs.update(re.findall(r"\d+", f.raw_text))

    director = TemplateStoryDirector()
    _, scenes = director.plan_narrative_arc(facts)
    for scene in scenes:
        for digit_run in re.findall(r"\d+", scene.narration_segment_text):
            assert digit_run in known_digit_runs, (
                f"scene {scene.narrative_role} narration contains digits {digit_run!r} "
                "not present in any verified fact"
            )
