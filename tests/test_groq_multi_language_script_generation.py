"""generate_scripts_with_claims_multi() — generates every target
language's narration+claims in ONE Groq call instead of one call per
language (added 2026-08-20 to cut the pipeline's Groq TPM usage: the
fact ledger + document context + prompt template were being resent in
full once per language). No real network calls — GroqManager.generate_json
is monkeypatched with a fake that inspects the prompt/call count instead
of hitting the API, matching this project's existing pattern of testing
the provider layer against a fake manager rather than mocking HTTP.
"""
from __future__ import annotations

from core.models.enums import Criticality, FactType, LanguageCode
from core.models.fact import SourceFact
from core.provenance.models import SourceSpan
from providers.llm.groq_client import GroqAllKeysExhaustedError, GroqManager
from providers.llm.groq_provider import GroqLLMProvider

PROJECT_ID = "proj-multilang-test"


def _make_facts() -> list[SourceFact]:
    return [
        SourceFact(
            project_id=PROJECT_ID, document_id="doc", fact_type=FactType.AMOUNT,
            value="₹2000", raw_text="a subsidy of ₹2000",
            source_span=SourceSpan(document_id="doc", page_number=1, text_span="a subsidy of ₹2000"),
            criticality=Criticality.HIGH, confidence=0.9, extractor_name="test",
        ),
    ]


def _combined_response(languages: list[LanguageCode]) -> dict:
    return {
        lang.value: {
            "narration_text": f"Narration in {lang.value}",
            "claims": [
                {"claim_text": f"claim for {lang.value}", "claim_type": "amount",
                 "criticality": "high", "source_fact_ids": []},
            ],
        }
        for lang in languages
    }


def test_combined_call_produces_every_language_from_one_generate_json_call(monkeypatch) -> None:
    manager = GroqManager(api_keys=["fake-key"])
    calls: list[str] = []

    def fake_generate_json(prompt: str, **kwargs):
        calls.append(prompt)
        return _combined_response([LanguageCode.EN, LanguageCode.HI, LanguageCode.MR])

    monkeypatch.setattr(manager, "generate_json", fake_generate_json)
    provider = GroqLLMProvider(manager)

    results = provider.generate_scripts_with_claims_multi(
        _make_facts(), "source context", [LanguageCode.EN, LanguageCode.HI, LanguageCode.MR],
        "farmers", 35, project_id=PROJECT_ID,
    )

    assert len(calls) == 1  # exactly one Groq call for all 3 languages
    assert set(results.keys()) == {LanguageCode.EN, LanguageCode.HI, LanguageCode.MR}
    for lang, (script, claims) in results.items():
        assert script.narration_text == f"Narration in {lang.value}"
        assert script.language == lang
        assert len(claims) == 1
        assert claims[0].claim_text == f"claim for {lang.value}"


def test_language_missing_from_combined_response_falls_back_individually(monkeypatch) -> None:
    manager = GroqManager(api_keys=["fake-key"])
    calls: list[str] = []

    def fake_generate_json(prompt: str, **kwargs):
        calls.append(prompt)
        if len(calls) == 1:
            # Combined call: MR is missing from the response entirely.
            return _combined_response([LanguageCode.EN, LanguageCode.HI])
        # Individual fallback call for the missing language.
        return {"narration_text": "Fallback MR narration", "claims": []}

    monkeypatch.setattr(manager, "generate_json", fake_generate_json)
    provider = GroqLLMProvider(manager)

    results = provider.generate_scripts_with_claims_multi(
        _make_facts(), "source context", [LanguageCode.EN, LanguageCode.HI, LanguageCode.MR],
        "farmers", 35, project_id=PROJECT_ID,
    )

    assert len(calls) == 2  # 1 combined + 1 individual fallback for MR only
    assert set(results.keys()) == {LanguageCode.EN, LanguageCode.HI, LanguageCode.MR}
    assert results[LanguageCode.MR][0].narration_text == "Fallback MR narration"
    assert results[LanguageCode.EN][0].narration_text == "Narration in en"


def test_combined_call_exhausted_falls_back_to_individual_calls_for_every_language(monkeypatch) -> None:
    manager = GroqManager(api_keys=["fake-key"])
    calls: list[str] = []

    def fake_generate_json(prompt: str, **kwargs):
        calls.append(prompt)
        if len(calls) == 1:
            raise GroqAllKeysExhaustedError("all keys exhausted (test)")
        # Individual fallback calls, one per language.
        return {"narration_text": "Individual narration", "claims": []}

    monkeypatch.setattr(manager, "generate_json", fake_generate_json)
    provider = GroqLLMProvider(manager)

    results = provider.generate_scripts_with_claims_multi(
        _make_facts(), "source context", [LanguageCode.EN, LanguageCode.HI],
        "farmers", 35, project_id=PROJECT_ID,
    )

    assert len(calls) == 3  # 1 failed combined + 2 individual fallbacks
    assert set(results.keys()) == {LanguageCode.EN, LanguageCode.HI}
    assert all(script.narration_text == "Individual narration" for script, _ in results.values())


def test_empty_language_list_returns_empty_dict_without_any_call(monkeypatch) -> None:
    manager = GroqManager(api_keys=["fake-key"])

    def fail_if_called(prompt: str, **kwargs):
        raise AssertionError("generate_json should not be called for an empty language list")

    monkeypatch.setattr(manager, "generate_json", fail_if_called)
    provider = GroqLLMProvider(manager)

    assert provider.generate_scripts_with_claims_multi(
        _make_facts(), "source context", [], "farmers", 35, project_id=PROJECT_ID,
    ) == {}
