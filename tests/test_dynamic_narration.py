"""dynamic_narration.generate_dynamic_narration — LLM-drafted, per-scene
narration grounded in each scene's own cited facts, with a real
DeterministicFactVerifier safety net: a scene whose LLM line invents
something not in its cited facts must revert to the deterministic
template line for THAT SCENE ONLY, never discarding the whole batch.
No network calls — GroqManager is faked throughout.
"""
from __future__ import annotations

from core.models.enums import Criticality, FactType, NarrativeRole
from core.models.fact import SourceFact
from core.provenance.models import SourceSpan
from providers.narrative.dynamic_narration import generate_dynamic_narration
from providers.narrative.template_story_director import TemplateStoryDirector

PROJECT_ID = "proj-test"
DOCUMENT_ID = "doc-test"


def _fact(fact_type: FactType, value: str, criticality: Criticality = Criticality.MEDIUM) -> SourceFact:
    return SourceFact(
        project_id=PROJECT_ID, document_id=DOCUMENT_ID, fact_type=fact_type, value=value,
        raw_text=value, source_span=SourceSpan(document_id=DOCUMENT_ID, page_number=1, text_span=value),
        criticality=criticality, confidence=0.95, extractor_name="manual-test-fixture",
    )


def _facts_and_scenes():
    facts = [
        _fact(FactType.ORGANIZATION, "Ministry of Finance"),
        _fact(FactType.SCHEME, "Income Tax Relief Scheme"),
        _fact(FactType.AMOUNT, "₹10,000", criticality=Criticality.CRITICAL),
        _fact(FactType.DEADLINE, "31 March 2026", criticality=Criticality.CRITICAL),
    ]
    _, scenes = TemplateStoryDirector().plan_narrative_arc(facts)
    return facts, scenes


class _FakeGroqManager:
    def __init__(self, *, response=None, raises: Exception | None = None) -> None:
        self.response = response
        self.raises = raises
        self.calls: list[str] = []

    def generate_json(self, prompt, *, temperature=0.2, **kwargs):
        self.calls.append(prompt)
        if self.raises is not None:
            raise self.raises
        return self.response


def test_uses_verified_llm_lines_when_grounded_in_cited_facts():
    facts, scenes = _facts_and_scenes()
    # one grounded line per scene, each using only that scene's own cited fact value(s)
    lines = []
    for scene in scenes:
        facts_by_id = {f.id: f for f in facts}
        cited = [facts_by_id[fid].value for fid in scene.source_fact_ids if fid in facts_by_id]
        lines.append(f"This is about {' and '.join(cited)}." if cited else "This concerns you.")
    fake = _FakeGroqManager(response={"narrations": lines})

    result = generate_dynamic_narration(scenes, facts, project_id=PROJECT_ID, groq_manager=fake)

    assert len(result) == len(scenes)
    assert [s.narration_segment_text for s in result] == lines
    # duration re-estimated from the new (different-length) text, not left stale
    for original, updated in zip(scenes, result):
        if original.narration_segment_text != updated.narration_segment_text:
            assert updated.duration_seconds > 0


def test_reverts_only_the_scene_that_fails_verification():
    facts, scenes = _facts_and_scenes()
    facts_by_id = {f.id: f for f in facts}

    lines = []
    announcement_index = None
    for i, scene in enumerate(scenes):
        if scene.narrative_role == NarrativeRole.ANNOUNCEMENT:
            announcement_index = i
            # invents an audience + a number nowhere in the facts — must fail verification
            lines.append("Farmers of Metropolis City receive ₹99,999 immediately.")
        else:
            cited = [facts_by_id[fid].value for fid in scene.source_fact_ids if fid in facts_by_id]
            lines.append(f"This concerns {' and '.join(cited)}." if cited else "This concerns you.")
    assert announcement_index is not None
    fake = _FakeGroqManager(response={"narrations": lines})

    result = generate_dynamic_narration(scenes, facts, project_id=PROJECT_ID, groq_manager=fake)

    # the invented line was rejected — that scene keeps its original deterministic text
    assert result[announcement_index].narration_segment_text == scenes[announcement_index].narration_segment_text
    assert "Metropolis" not in result[announcement_index].narration_segment_text
    # every other scene's verified LLM line was still accepted
    for i, (original, updated) in enumerate(zip(scenes, result)):
        if i != announcement_index:
            assert updated.narration_segment_text == lines[i]


def test_falls_back_to_deterministic_on_groq_exhaustion():
    from providers.llm.groq_client import GroqAllKeysExhaustedError

    facts, scenes = _facts_and_scenes()
    fake = _FakeGroqManager(raises=GroqAllKeysExhaustedError("all keys dead"))

    result = generate_dynamic_narration(scenes, facts, project_id=PROJECT_ID, groq_manager=fake)

    assert [s.narration_segment_text for s in result] == [s.narration_segment_text for s in scenes]


def test_falls_back_on_count_mismatch():
    facts, scenes = _facts_and_scenes()
    fake = _FakeGroqManager(response={"narrations": ["only one line"]})  # scenes has more than 1

    result = generate_dynamic_narration(scenes, facts, project_id=PROJECT_ID, groq_manager=fake)

    assert [s.narration_segment_text for s in result] == [s.narration_segment_text for s in scenes]


def test_falls_back_on_empty_line():
    facts, scenes = _facts_and_scenes()
    fake = _FakeGroqManager(response={"narrations": ["   "] * len(scenes)})

    result = generate_dynamic_narration(scenes, facts, project_id=PROJECT_ID, groq_manager=fake)

    assert [s.narration_segment_text for s in result] == [s.narration_segment_text for s in scenes]


def test_unwraps_an_object_wrapped_response():
    """Regression guard for the same Groq JSON-mode gotcha already fixed
    live for generate_fact_aware_image_prompts: a bare top-level array
    gets rejected by Groq's JSON mode even when well-formed — the prompt
    asks for {"narrations": [...]}, which must be unwrapped."""
    facts, scenes = _facts_and_scenes()
    facts_by_id = {f.id: f for f in facts}
    lines = []
    for scene in scenes:
        cited = [facts_by_id[fid].value for fid in scene.source_fact_ids if fid in facts_by_id]
        lines.append(f"This concerns {' and '.join(cited)}." if cited else "This concerns you.")
    fake = _FakeGroqManager(response={"narrations": lines})

    result = generate_dynamic_narration(scenes, facts, project_id=PROJECT_ID, groq_manager=fake)

    assert [s.narration_segment_text for s in result] == lines


def test_empty_scenes_list_returns_empty_list():
    facts, _ = _facts_and_scenes()
    fake = _FakeGroqManager(response={"narrations": []})
    assert generate_dynamic_narration([], facts, project_id=PROJECT_ID, groq_manager=fake) == []
    assert fake.calls == []  # never even calls Groq for an empty scene list
