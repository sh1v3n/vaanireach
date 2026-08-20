"""DeterministicFactVerifier — must both (a) pass every real claim the
guaranteed pipeline actually produces, and (b) genuinely CATCH each of
the 7 violation categories Step E requires (invented numbers, dates,
names, locations, unsupported claims, unsupported URLs/phone numbers,
banned relative-temporal language). A verifier that only ever passes
clean data proves nothing — every category below has its own
deliberately-broken test case.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from core.models.claim import Claim  # noqa: E402
from core.models.enums import Criticality, LanguageCode, VerificationStatus  # noqa: E402
from core.models.fact import SourceFact  # noqa: E402
from providers.narrative.template_story_director import TemplateStoryDirector  # noqa: E402
from providers.verification.deterministic_fact_verifier import (  # noqa: E402
    DeterministicFactVerifier, claims_from_scenes,
)
from tests.test_narrative_story_director import sample_notice_facts  # noqa: E402


@pytest.fixture(scope="module")
def facts() -> list[SourceFact]:
    return sample_notice_facts()


@pytest.fixture(scope="module")
def real_claims_and_scenes(facts):
    _, scenes = TemplateStoryDirector().plan_narrative_arc(facts)
    claims = claims_from_scenes(scenes)
    return claims, scenes


def _claim(text: str, fact_ids: list[str], project_id: str = "p1") -> Claim:
    return Claim(
        project_id=project_id,
        claim_text=text,
        language=LanguageCode.EN,
        source_fact_ids=fact_ids,
        claim_type="test",
        criticality=Criticality.CRITICAL,
    )


# ---------------------------------------------------------------- clean data must pass

def test_every_real_claim_from_the_actual_pipeline_passes(facts, real_claims_and_scenes):
    claims, _ = real_claims_and_scenes
    verifier = DeterministicFactVerifier()
    results = verifier.verify_batch(claims, facts)
    assert len(results) == len(claims)
    for result, claim in zip(results, claims):
        assert result.status == VerificationStatus.VERIFIED, (
            f"real claim unexpectedly failed verification: {claim.claim_text!r} -> {result.explanation}"
        )
        assert result.is_blocking is False


def test_claims_from_scenes_preserves_fact_and_claim_ids(real_claims_and_scenes):
    """Requirement 10: claim_ids/source_fact_ids preserved through the pipeline."""
    claims, scenes = real_claims_and_scenes
    claims_by_id = {c.id: c for c in claims}
    for scene in scenes:
        assert len(scene.claim_ids) == 1
        claim = claims_by_id[scene.claim_ids[0]]
        assert claim.source_fact_ids == scene.source_fact_ids
        assert claim.claim_text == scene.narration_segment_text


# ---------------------------------------------------------------- each category must be CAUGHT

def test_catches_invented_number(facts):
    claim = _claim("Eligible recipients receive ₹9,999.", [facts[4].id])  # real fact is ₹2,000
    result = DeterministicFactVerifier().verify_claim(claim, facts)
    assert result.status != VerificationStatus.VERIFIED
    assert result.is_blocking is True


def test_catches_invented_date(facts):
    claim = _claim("Applications close on 1 January 2099.", [facts[5].id])  # real deadline is 31 March 2026
    result = DeterministicFactVerifier().verify_claim(claim, facts)
    assert result.status != VerificationStatus.VERIFIED
    assert result.is_blocking is True


def test_catches_invented_name_or_location(facts):
    claim = _claim("This announcement comes from the Office of Northgate Valley.", [facts[0].id])
    result = DeterministicFactVerifier().verify_claim(claim, facts)
    assert result.status != VerificationStatus.VERIFIED
    assert result.is_blocking is True


def test_catches_unsupported_claim_citing_unknown_fact_id(facts):
    claim = _claim("Farmers of Riverbend District — this concerns you.", ["fact-id-not-in-the-ledger"])
    result = DeterministicFactVerifier().verify_claim(claim, facts)
    assert result.status != VerificationStatus.VERIFIED
    assert result.is_blocking is True


def test_catches_unsupported_url(facts):
    claim = _claim("Apply at www.totally-fake-domain.example.", [facts[6].id])  # real url is example-scheme.gov.in
    result = DeterministicFactVerifier().verify_claim(claim, facts)
    assert result.status != VerificationStatus.VERIFIED
    assert result.is_blocking is True


def test_catches_unsupported_phone_number(facts):
    claim = _claim("Call our helpline at 1800-999-9999.", [facts[8].id])  # real phone is 1800-000-0000
    result = DeterministicFactVerifier().verify_claim(claim, facts)
    assert result.status != VerificationStatus.VERIFIED
    assert result.is_blocking is True


@pytest.mark.parametrize("phrase", ["today", "time is running out", "act now", "hurry"])
def test_catches_banned_relative_temporal_language(facts, phrase):
    claim = _claim(f"Submit your application {phrase}, before 31 March 2026.", [facts[5].id])
    result = DeterministicFactVerifier().verify_claim(claim, facts)
    assert result.status != VerificationStatus.VERIFIED
    assert result.is_blocking is True
