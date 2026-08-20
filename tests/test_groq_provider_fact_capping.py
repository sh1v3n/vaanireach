"""_prioritized_facts() — caps the fact ledger embedded in
generate_script_with_claims's prompt (added 2026-08-20 to reduce Groq TPM
usage: that call runs once per target language, and an uncapped ledger
was being resent in full every time, competing for the same shared
budget — see groq_client.py's module docstring). Keeps the highest-
criticality facts rather than truncating in extraction order.
"""
from __future__ import annotations

from core.models.enums import Criticality, FactType
from core.models.fact import SourceFact
from core.provenance.models import SourceSpan
from providers.llm.groq_provider import SCRIPT_GENERATION_FACTS_LIMIT, _prioritized_facts


def _make_fact(i: int, criticality: Criticality, confidence: float) -> SourceFact:
    return SourceFact(
        project_id="proj", document_id="doc", fact_type=FactType.STATISTIC,
        value=f"v{i}", raw_text=f"r{i}",
        source_span=SourceSpan(document_id="doc", page_number=1, text_span=f"r{i}"),
        criticality=criticality, confidence=confidence, extractor_name="test",
    )


def test_under_limit_returns_all_facts_unchanged() -> None:
    facts = [_make_fact(i, Criticality.LOW, 0.5) for i in range(10)]
    assert _prioritized_facts(facts, 40) == facts


def test_over_limit_keeps_highest_criticality_facts_first() -> None:
    low_facts = [_make_fact(i, Criticality.LOW, 0.5) for i in range(50)]
    critical_facts = [_make_fact(100 + i, Criticality.CRITICAL, 0.9) for i in range(5)]
    facts = low_facts + critical_facts

    result = _prioritized_facts(facts, SCRIPT_GENERATION_FACTS_LIMIT)

    assert len(result) == SCRIPT_GENERATION_FACTS_LIMIT
    # every critical fact must survive the cap even though it was appended last
    assert all(f.criticality == Criticality.CRITICAL for f in result[:5])
    assert {f.value for f in critical_facts}.issubset({f.value for f in result})


def test_ties_broken_by_confidence_descending() -> None:
    facts = [_make_fact(i, Criticality.HIGH, conf) for i, conf in enumerate([0.2, 0.9, 0.5])]
    result = _prioritized_facts(facts, 2)
    assert [f.confidence for f in result] == [0.9, 0.5]
