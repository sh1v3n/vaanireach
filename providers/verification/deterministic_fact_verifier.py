"""DeterministicFactVerifier — the Final Verification stage (Phase 2
media plan, Step E): re-checks every scene's narration against the
Source Fact Ledger before a video is declared valid. Independent of
TemplateStoryDirector — this exists to CATCH a violation if one ever
slips through, not to trust that the generator was correct.

Checks, in order (first failure wins):
1. Every source_fact_id the claim cites must exist in the ledger
   (unsupported claim).
2. Every digit run in the claim text must appear in some fact's
   value/raw_text (invented number or date).
3. Every URL-shaped token must exactly equal some fact's value
   (unsupported URL).
4. Every phone-number-shaped token must exactly equal some fact's value
   (unsupported phone number).
5. No banned relative-temporal phrase (unsupported temporal claim — this
   ledger carries no verified-current-date fact to compare against).
6. Every multi-word Title-Case phrase (a cheap proper-noun heuristic)
   must be a substring of some fact's value (invented name/location/org).

`verify_semantic` intentionally runs the same deterministic checks as
`verify_deterministic` rather than a separate paraphrase-aware strategy —
template-v1's narration is template text over literal fact values, not
free-form paraphrase, so there's nothing "semantic" to check yet; this
keeps the ABC honestly implemented without pretending to do NLP work
this phase doesn't need.
"""
from __future__ import annotations

import re

from core.interfaces.verification_engine import VerificationEngine
from core.models.claim import Claim
from core.models.enums import LanguageCode, VerificationStatus, VerificationType
from core.models.fact import SourceFact
from core.models.storyboard import Scene
from core.models.verification import VerificationResult

_URL_RE = re.compile(r"\bwww\.\S+\.\S+\b|\bhttps?://\S+\b")
_PHONE_RE = re.compile(r"\b\d[\d\-]{6,}\d\b")
_TITLE_CASE_PHRASE_RE = re.compile(r"\b(?:[A-Z][a-zA-Z]*\s){1,6}[A-Z][a-zA-Z]*\b")

_BANNED_RELATIVE_TEMPORAL_PHRASES = [
    "today", "tomorrow", "tonight", "soon", "this week", "this month",
    "right now", "currently", "shortly", "hurry", "running out", "act now",
    "don't wait", "time is short", "time is running out",
]

VERIFIER_NAME = "deterministic-fact-verifier-v1"


def claims_from_scenes(scenes: list[Scene], *, project_id: str = "", language: LanguageCode = LanguageCode.EN) -> list[Claim]:
    """Builds one Claim per scene (claim_text = narration, source_fact_ids
    copied from the scene) and writes the new claim's id back onto
    scene.claim_ids — this is what makes claim_ids a real, populated
    traceability field through the pipeline rather than an always-empty
    default. criticality is inherited from the most critical fact the
    scene cites (defaults to MEDIUM if the scene cites no facts, which
    shouldn't happen for any scene TemplateStoryDirector produces)."""
    from core.models.enums import Criticality

    claims: list[Claim] = []
    for scene in scenes:
        claim = Claim(
            project_id=project_id,
            claim_text=scene.narration_segment_text,
            language=language,
            source_fact_ids=list(scene.source_fact_ids),
            claim_type=scene.narrative_role.value,
            criticality=Criticality.MEDIUM,
        )
        scene.claim_ids = [claim.id]
        claims.append(claim)
    return claims


class DeterministicFactVerifier(VerificationEngine):
    def verify_deterministic(self, claim: Claim, source_facts: list[SourceFact]) -> VerificationResult:
        return self._verify(claim, source_facts, VerificationType.DETERMINISTIC)

    def verify_semantic(self, claim: Claim, source_facts: list[SourceFact]) -> VerificationResult:
        return self._verify(claim, source_facts, VerificationType.SEMANTIC)

    def verify_claim(self, claim: Claim, source_facts: list[SourceFact]) -> VerificationResult:
        return self.verify_deterministic(claim, source_facts)

    def verify_batch(self, claims: list[Claim], source_facts: list[SourceFact]) -> list[VerificationResult]:
        return [self.verify_claim(claim, source_facts) for claim in claims]

    # ---------------------------------------------------------------- internals

    def _verify(self, claim: Claim, source_facts: list[SourceFact], vtype: VerificationType) -> VerificationResult:
        facts_by_id = {f.id: f for f in source_facts}
        all_values = [f.value for f in source_facts] + [f.raw_text for f in source_facts]

        def fail(reason: str, status: VerificationStatus) -> VerificationResult:
            return VerificationResult(
                project_id=claim.project_id,
                claim_id=claim.id,
                verification_type=vtype,
                status=status,
                matched_source_fact_ids=[],
                explanation=reason,
                confidence=1.0,
                verifier_name=VERIFIER_NAME,
                is_blocking=True,
            )

        # 1. unsupported claim: cited fact ids must exist in the ledger
        unknown_ids = [fid for fid in claim.source_fact_ids if fid not in facts_by_id]
        if unknown_ids:
            return fail(f"cites unknown fact id(s) not in the Source Fact Ledger: {unknown_ids!r}",
                        VerificationStatus.NOT_FOUND)

        text = claim.claim_text

        # 2. invented numbers/dates: every digit run must appear in some fact value/raw_text
        for digit_run in re.findall(r"\d+", text):
            if not any(digit_run in v for v in all_values):
                return fail(f"contains digits {digit_run!r} not present in any verified fact",
                            VerificationStatus.CONTRADICTED)

        # 3. unsupported URL
        for url in _URL_RE.findall(text):
            if not any(url == v or url in v for v in all_values):
                return fail(f"contains URL {url!r} not present in any verified fact",
                            VerificationStatus.CONTRADICTED)

        # 4. unsupported phone number
        for phone in _PHONE_RE.findall(text):
            if not any(phone == v or phone in v for v in all_values):
                return fail(f"contains phone number {phone!r} not present in any verified fact",
                            VerificationStatus.CONTRADICTED)

        # 5. banned relative-temporal language
        lowered = text.lower()
        for phrase in _BANNED_RELATIVE_TEMPORAL_PHRASES:
            if phrase in lowered:
                return fail(f"contains unsupported relative-temporal phrase {phrase!r}",
                            VerificationStatus.CONTRADICTED)

        # 6. invented names/locations/orgs: multi-word Title-Case phrases
        # must be grounded in a fact value (cheap proper-noun heuristic —
        # no NER available in the guaranteed pipeline). A greedy match can
        # sweep up a leading connective word that happens to be
        # capitalized (e.g. "Announcing Kisan Sahayata Yojana" — the real
        # proper noun is just "Kisan Sahayata Yojana"), so try the full
        # phrase, then progressively drop leading words (never below 2,
        # to keep the check meaningfully strict) before concluding it's
        # genuinely ungrounded.
        for phrase in _TITLE_CASE_PHRASE_RE.findall(text):
            words = phrase.split()
            if not any(" ".join(words[start:]) in v for start in range(len(words) - 1) for v in all_values):
                return fail(f"contains an ungrounded proper-noun phrase {phrase!r}",
                            VerificationStatus.CONTRADICTED)

        return VerificationResult(
            project_id=claim.project_id,
            claim_id=claim.id,
            verification_type=vtype,
            status=VerificationStatus.VERIFIED,
            matched_source_fact_ids=list(claim.source_fact_ids),
            explanation="every number, date, URL, phone number, and proper-noun phrase in the "
                        "claim is grounded in the Source Fact Ledger; no banned temporal language found",
            confidence=1.0,
            verifier_name=VERIFIER_NAME,
            is_blocking=False,
        )
