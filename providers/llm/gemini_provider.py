"""GeminiLLMProvider — a single Gemini-backed implementation of four Phase 0
interfaces: FactExtractor, ScriptGenerator, TranslationProvider, and (the
semantic half of) VerificationEngine. Deterministic verification is pure
Python (regex + rapidfuzz) and never touches the network, so it can never
be rate-limited and always runs even if every Gemini key is dead.

Every Gemini call in this file goes through one shared GeminiManager, so
the whole provider benefits from a single horizontal key-rotation pool
(see gemini_client.py) — nothing here talks to the SDK directly.

Two documented, deliberate deviations from the strict Phase 0 interfaces
(both explained where they occur below):
  1. `extract_facts` and `generate_script` gain a required keyword-only
     `project_id` argument — the ABC signatures omit it even though
     SourceFact/Script both require it.
  2. `generate_script`/`regenerate_script` are thin wrappers around richer
     `..._with_claims` methods, because Phase 0 has no dedicated
     ClaimExtractor interface even though "Claim Extraction" is its own
     documented pipeline stage. The orchestrator should call the richer
     methods directly.
"""
from __future__ import annotations

import logging
import re

from core.interfaces.fact_extractor import FactExtractor
from core.interfaces.script_generator import ScriptGenerator
from core.interfaces.translation_provider import TranslationProvider
from core.interfaces.verification_engine import VerificationEngine
from core.models.claim import Claim
from core.models.document import DocumentPage
from core.models.enums import (
    Criticality,
    FactType,
    LanguageCode,
    ScriptStatus,
    VerificationStatus,
    VerificationType,
)
from core.models.fact import SourceFact
from core.models.script import Script
from core.models.verification import VerificationResult
from core.provenance.models import SourceSpan
from providers.llm.gemini_client import GeminiAllKeysExhaustedError, GeminiManager

try:
    from rapidfuzz import fuzz

    def _similarity(a: str, b: str) -> float:
        return float(fuzz.token_set_ratio(a, b))

except ImportError:  # pragma: no cover - only if rapidfuzz isn't installed
    from difflib import SequenceMatcher

    def _similarity(a: str, b: str) -> float:
        return SequenceMatcher(None, a.lower(), b.lower()).ratio() * 100.0


logger = logging.getLogger("vaanireach.providers.gemini_provider")

GEMINI_MODEL = "gemini-3.6-flash"  # see providers/llm/gemini_client.py's DEFAULT_TEXT_MODEL docstring

# claim_type values (free-form per the Claim model) that get objectively
# checked against source facts with no LLM call — numbers/dates/etc. are
# exactly the FactTypes ADR-002 designates for deterministic verification.
DETERMINISTIC_CLAIM_TYPES = {
    "amount", "date", "deadline", "percentage", "phone_number", "url",
    "number", "statistic", "scheme", "location",
}
FUZZY_MATCH_THRESHOLD = 85.0

_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*")
_STATUS_MAP = {
    "verified": VerificationStatus.VERIFIED,
    "contradicted": VerificationStatus.CONTRADICTED,
    "not_found": VerificationStatus.NOT_FOUND,
    "uncertain": VerificationStatus.UNCERTAIN,
}


def _normalize_numbers(text: str) -> set[str]:
    return {m.replace(",", "") for m in _NUMBER_RE.findall(text)}


# --------------------------------------------------------------------------- prompts

FACT_EXTRACTION_PROMPT = """You are a meticulous fact-extraction assistant for an Indian \
government outreach system. Read the official notice below and extract every discrete, \
checkable fact.

Valid fact_type values: {fact_types}

Return ONLY a JSON array. Each element must have exactly these keys:
- "fact_type": one of the valid fact_type values above
- "value": the normalized value, ALWAYS translated into English regardless of what \
language the source document is written in (e.g. "₹2000", "2026-03-31", "Pune district", \
"Free Textbook Distribution Scheme"). This field is the language-neutral fact ledger every \
target-language video (including an English one) gets generated from, so it must never be \
left in Hindi/Marathi/any other source language — translate it, don't just copy it.
- "raw_text": the exact verbatim text this was extracted from, in the ORIGINAL source \
language — do not translate this one, it exists for provenance/citation.
- "page_number": integer page number it appears on
- "section_heading": the nearest section heading, or null
- "criticality": one of "low", "medium", "high", "critical" — how much a wrong/missing \
value here would matter if published (deadlines/amounts/eligibility are usually high or \
critical; a background org name is usually low or medium)
- "confidence": a number from 0.0 to 1.0

Do not invent facts that are not explicitly present in the text. Do not add commentary or \
markdown fences — output the raw JSON array only.

DOCUMENT:
{document_text}
"""

SCRIPT_GENERATION_PROMPT = """You are writing a {duration}-second spoken-narration video \
script in {language} for an Indian government outreach video, aimed at: {audience}.

Ground every sentence ONLY in the facts below — never invent a date, amount, name, or \
number that isn't listed. Aim for roughly {target_words} words (natural spoken pace). Write \
warmly and conversationally, not like a legal notice.

SOURCE FACTS (id, type, criticality, value, raw text):
{facts_block}

ORIGINAL DOCUMENT CONTEXT (for tone/context only — facts above are authoritative):
{source_context}

Return ONLY a JSON object with exactly these keys:
- "narration_text": the full narration script, in {language}, as one string
- "claims": an array of every checkable statement the narration makes, each an object with:
  - "claim_text": the exact sentence/phrase from narration_text
  - "claim_type": a short free-form label, e.g. "amount", "deadline", "eligibility", "location"
  - "criticality": "low" | "medium" | "high" | "critical"
  - "source_fact_ids": array of the fact ids (from the bracketed [id] above) this claim is \
grounded in

No markdown fences, no commentary — the raw JSON object only.
"""

SCRIPT_REGENERATION_PROMPT = """Revise the {duration}-second {language} narration script \
below for audience: {audience}. Verification found problems — fix ONLY what's listed, \
keep everything else as close to the original as possible.

PREVIOUS NARRATION:
{previous_narration}

VERIFICATION ISSUES TO FIX:
{issues_block}

Return ONLY a JSON object with the same shape as before: "narration_text" (the corrected \
full script) and "claims" (array of {{claim_text, claim_type, criticality, source_fact_ids}}). \
No markdown fences, no commentary.
"""

TRANSLATE_TEXT_PROMPT = """Translate the following text from {source} to {target}. Preserve \
all numbers, dates, amounts, names, and URLs EXACTLY as written — do not localize or \
alter them. Return ONLY the translated text, nothing else.

TEXT:
{text}
"""

TRANSLATE_CLAIMS_PROMPT = """Translate each numbered statement below into {target}. Preserve \
every number, date, amount, name, and URL EXACTLY as written — never alter or localize \
them. Return ONLY a JSON array of strings, same order and same length as the input, one \
translated string per statement — no commentary, no markdown fences.

STATEMENTS:
{claims_block}
"""

SEMANTIC_VERIFY_PROMPT = """You are a strict fact-checker. Decide whether the CLAIM below is \
supported by the SOURCE FACTS. The claim may be a paraphrase, an eligibility statement, or \
translated text — judge meaning, not exact wording.

SOURCE FACTS:
{facts_block}

CLAIM:
{claim_text}

Return ONLY a JSON object with keys:
- "status": one of "verified" | "contradicted" | "not_found" | "uncertain"
- "matched_source_fact_ids": array of the fact ids (the bracketed [id]s above) that support \
your verdict
- "explanation": one concise sentence
- "confidence": 0.0 to 1.0
No markdown fences, no commentary.
"""

SEMANTIC_VERIFY_BATCH_PROMPT = """You are a strict fact-checker. For EACH claim below, decide \
whether it is supported by the SOURCE FACTS. Claims may be paraphrases, eligibility \
statements, or translated text — judge meaning, not exact wording.

SOURCE FACTS:
{facts_block}

CLAIMS (id: text):
{claims_block}

Return ONLY a JSON array with one object per claim, each with keys:
- "claim_id": the claim's id, copied exactly from the bracketed [id] above
- "status": one of "verified" | "contradicted" | "not_found" | "uncertain"
- "matched_source_fact_ids": array of supporting fact ids
- "explanation": one concise sentence
- "confidence": 0.0 to 1.0
No markdown fences, no commentary — return every claim, in any order.
"""


# --------------------------------------------------------------------------- json -> model helpers

def _render_pages_for_prompt(pages: list[DocumentPage]) -> str:
    return "\n\n".join(f"--- Page {p.page_number} ---\n{p.raw_text}" for p in pages)


def _render_facts_for_prompt(facts: list[SourceFact]) -> str:
    if not facts:
        return "(no facts extracted)"
    return "\n".join(
        f'[{f.id}] ({f.fact_type.value}, {f.criticality.value}) {f.value} — "{f.raw_text}"'
        for f in facts
    )


def _fact_from_json(item: dict, *, document_id: str, project_id: str) -> SourceFact:
    fact_type_raw = str(item["fact_type"]).strip().lower().replace(" ", "_")
    fact_type = FactType(fact_type_raw)
    criticality = Criticality(str(item.get("criticality", "medium")).strip().lower())
    confidence = max(0.0, min(1.0, float(item.get("confidence", 0.7))))
    raw_text = str(item.get("raw_text", item.get("value", ""))).strip()
    page_number = int(item.get("page_number") or 1)
    return SourceFact(
        project_id=project_id,
        document_id=document_id,
        fact_type=fact_type,
        value=str(item["value"]).strip(),
        raw_text=raw_text,
        source_span=SourceSpan(
            document_id=document_id,
            page_number=page_number,
            text_span=raw_text,
            section_heading=item.get("section_heading") or None,
        ),
        criticality=criticality,
        confidence=confidence,
        extractor_name=GEMINI_MODEL,
    )


def _claim_from_json(item: dict, *, project_id: str, script_id: str, language: LanguageCode) -> Claim:
    criticality = Criticality(str(item.get("criticality", "medium")).strip().lower())
    source_fact_ids = [str(x) for x in item.get("source_fact_ids", [])]
    return Claim(
        project_id=project_id,
        script_id=script_id,
        claim_text=str(item["claim_text"]).strip(),
        language=language,
        source_fact_ids=source_fact_ids,
        claim_type=str(item.get("claim_type", "statement")).strip().lower(),
        criticality=criticality,
    )


def _relevant_facts(claim: Claim, source_facts: list[SourceFact]) -> list[SourceFact]:
    if claim.source_fact_ids:
        by_id = {f.id: f for f in source_facts}
        found = [by_id[fid] for fid in claim.source_fact_ids if fid in by_id]
        if found:
            return found
    return [f for f in source_facts if f.project_id == claim.project_id]


def _verification_result_from_json(item: dict, *, claim: Claim, verifier_name: str) -> VerificationResult:
    status = _STATUS_MAP.get(str(item["status"]).strip().lower(), VerificationStatus.UNCERTAIN)
    confidence = max(0.0, min(1.0, float(item.get("confidence", 0.5))))
    matched_ids = [str(x) for x in item.get("matched_source_fact_ids", [])]
    is_blocking = status != VerificationStatus.VERIFIED and claim.criticality in (
        Criticality.HIGH, Criticality.CRITICAL,
    )
    return VerificationResult(
        project_id=claim.project_id,
        claim_id=claim.id,
        verification_type=VerificationType.SEMANTIC,
        status=status,
        matched_source_fact_ids=matched_ids,
        explanation=str(item.get("explanation", "")).strip(),
        confidence=confidence,
        verifier_name=verifier_name,
        is_blocking=is_blocking,
    )


# --------------------------------------------------------------------------- provider

class GeminiLLMProvider(FactExtractor, ScriptGenerator, TranslationProvider, VerificationEngine):
    def __init__(self, manager: GeminiManager | None = None) -> None:
        self.manager = manager or GeminiManager()

    # ---------------------------------------------------------------- FactExtractor

    def supported_fact_types(self) -> list[FactType]:
        return list(FactType)

    def extract_facts(
        self,
        document_id: str,
        pages: list[DocumentPage],
        *,
        project_id: str,
    ) -> list[SourceFact]:
        """See module docstring re: the added `project_id` keyword-only
        argument — a documented gap between this ABC's signature and
        SourceFact's required `project_id` field."""
        if not pages:
            return []

        fact_types = ", ".join(t.value for t in FactType)
        prompt = FACT_EXTRACTION_PROMPT.format(
            fact_types=fact_types, document_text=_render_pages_for_prompt(pages)
        )

        try:
            raw = self.manager.generate_json(prompt, model=GEMINI_MODEL, temperature=0.1)
        except GeminiAllKeysExhaustedError:
            logger.error("extract_facts: all Gemini keys exhausted — returning empty fact ledger")
            return []

        if isinstance(raw, dict):
            raw = raw.get("facts", [])
        items = raw if isinstance(raw, list) else []

        facts: list[SourceFact] = []
        for item in items:
            try:
                facts.append(_fact_from_json(item, document_id=document_id, project_id=project_id))
            except Exception as exc:  # noqa: BLE001 - one malformed fact must not drop the rest
                logger.warning("extract_facts: skipping malformed fact %r: %s", item, exc)
        return facts

    # ---------------------------------------------------------------- ScriptGenerator

    def generate_script(
        self,
        source_facts: list[SourceFact],
        source_context: str,
        target_language: LanguageCode,
        audience: str,
        desired_duration_seconds: int,
        *,
        project_id: str,
    ) -> Script:
        """See module docstring re: the added `project_id` argument and
        the claims this drops — call `generate_script_with_claims`
        directly to get both."""
        script, _claims = self.generate_script_with_claims(
            source_facts, source_context, target_language, audience,
            desired_duration_seconds, project_id=project_id,
        )
        return script

    def generate_script_with_claims(
        self,
        source_facts: list[SourceFact],
        source_context: str,
        target_language: LanguageCode,
        audience: str,
        desired_duration_seconds: int,
        *,
        project_id: str,
    ) -> tuple[Script, list[Claim]]:
        target_words = max(40, int(desired_duration_seconds * 2.3))
        prompt = SCRIPT_GENERATION_PROMPT.format(
            language=target_language.value,
            audience=audience,
            duration=desired_duration_seconds,
            target_words=target_words,
            facts_block=_render_facts_for_prompt(source_facts),
            source_context=(source_context or "")[:4000],
        )

        raw = self.manager.generate_json(prompt, model=GEMINI_MODEL, temperature=0.5)
        if not isinstance(raw, dict):
            logger.warning("generate_script: expected a JSON object, got %s", type(raw))
            raw = {}

        script = Script(
            project_id=project_id,
            language=target_language,
            audience=audience,
            target_duration_seconds=desired_duration_seconds,
            narration_text=str(raw.get("narration_text", "")).strip(),
            source_fact_ids=[f.id for f in source_facts],
            generator_name=GEMINI_MODEL,
            version=1,
            status=ScriptStatus.DRAFT,
        )

        claims: list[Claim] = []
        for item in raw.get("claims", []) if isinstance(raw.get("claims"), list) else []:
            try:
                claims.append(_claim_from_json(item, project_id=project_id, script_id=script.id, language=target_language))
            except Exception as exc:  # noqa: BLE001
                logger.warning("generate_script: skipping malformed claim %r: %s", item, exc)

        script.claim_ids = [c.id for c in claims]
        return script, claims

    def regenerate_script(
        self,
        previous_script: Script,
        verification_results: list[VerificationResult],
    ) -> Script:
        script, _claims = self.regenerate_script_with_claims(previous_script, verification_results)
        return script

    def regenerate_script_with_claims(
        self,
        previous_script: Script,
        verification_results: list[VerificationResult],
    ) -> tuple[Script, list[Claim]]:
        failed = [
            r for r in verification_results
            if r.status in (VerificationStatus.CONTRADICTED, VerificationStatus.NOT_FOUND)
        ]
        issues_block = "\n".join(
            f"- claim_id={r.claim_id}: {r.status.value} — {r.explanation}" for r in failed
        ) or "(no specific failures listed — improve overall factual grounding)"

        prompt = SCRIPT_REGENERATION_PROMPT.format(
            language=previous_script.language.value,
            audience=previous_script.audience,
            duration=previous_script.target_duration_seconds,
            previous_narration=previous_script.narration_text,
            issues_block=issues_block,
        )

        try:
            raw = self.manager.generate_json(prompt, model=GEMINI_MODEL, temperature=0.5)
        except GeminiAllKeysExhaustedError:
            logger.error("regenerate_script: all Gemini keys exhausted — keeping previous script unchanged")
            return previous_script, []

        if not isinstance(raw, dict):
            logger.warning("regenerate_script: expected a JSON object, got %s", type(raw))
            raw = {}

        new_script = Script(
            project_id=previous_script.project_id,
            language=previous_script.language,
            audience=previous_script.audience,
            target_duration_seconds=previous_script.target_duration_seconds,
            narration_text=str(raw.get("narration_text", "")).strip() or previous_script.narration_text,
            source_fact_ids=previous_script.source_fact_ids,
            generator_name=GEMINI_MODEL,
            version=previous_script.version + 1,
            status=ScriptStatus.REGENERATING,
        )

        claims: list[Claim] = []
        for item in raw.get("claims", []) if isinstance(raw.get("claims"), list) else []:
            try:
                claims.append(
                    _claim_from_json(
                        item, project_id=previous_script.project_id,
                        script_id=new_script.id, language=previous_script.language,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("regenerate_script: skipping malformed claim %r: %s", item, exc)

        new_script.claim_ids = [c.id for c in claims]
        return new_script, claims

    # ---------------------------------------------------------------- TranslationProvider

    def supported_languages(self) -> list[LanguageCode]:
        return list(LanguageCode)

    def translate(self, text: str, source_language: LanguageCode, target_language: LanguageCode) -> str:
        if source_language == target_language or not text.strip():
            return text
        prompt = TRANSLATE_TEXT_PROMPT.format(
            source=source_language.value, target=target_language.value, text=text,
        )
        try:
            return self.manager.generate_text(prompt, model=GEMINI_MODEL, temperature=0.3).strip()
        except GeminiAllKeysExhaustedError:
            logger.error("translate: all Gemini keys exhausted — returning source text unchanged")
            return text

    def translate_claims(self, claims: list[Claim], target_language: LanguageCode) -> list[Claim]:
        if not claims:
            return []
        pending = [c for c in claims if c.language != target_language]
        if not pending:
            return list(claims)

        prompt = TRANSLATE_CLAIMS_PROMPT.format(
            target=target_language.value,
            claims_block="\n".join(f"[{i}] {c.claim_text}" for i, c in enumerate(pending)),
        )

        translated_by_id: dict[str, str] = {}
        try:
            raw = self.manager.generate_json(prompt, model=GEMINI_MODEL, temperature=0.3)
            if isinstance(raw, list) and len(raw) == len(pending):
                translated_by_id = {c.id: str(t) for c, t in zip(pending, raw)}
            else:
                logger.warning("translate_claims: batch response shape mismatch — falling back to per-claim calls")
        except (GeminiAllKeysExhaustedError, ValueError) as exc:
            logger.warning("translate_claims: batch translation failed (%s) — falling back to per-claim calls", exc)

        results: list[Claim] = []
        for claim in claims:
            if claim.language == target_language:
                results.append(claim)
                continue
            new_text = translated_by_id.get(claim.id) or self.translate(claim.claim_text, claim.language, target_language)
            results.append(
                Claim(
                    project_id=claim.project_id,
                    script_id=claim.script_id,
                    translation_id=claim.translation_id,
                    claim_text=new_text,
                    language=target_language,
                    source_fact_ids=claim.source_fact_ids,
                    claim_type=claim.claim_type,
                    criticality=claim.criticality,
                )
            )
        return results

    # ---------------------------------------------------------------- VerificationEngine

    def verify_deterministic(self, claim: Claim, source_facts: list[SourceFact]) -> VerificationResult:
        """Pure Python, zero network calls — numbers/dates/amounts/etc.
        are compared against linked source facts via normalized-number
        matching first, fuzzy text matching second."""
        relevant = _relevant_facts(claim, source_facts)
        claim_numbers = _normalize_numbers(claim.claim_text)

        best_match: SourceFact | None = None
        best_score = 0.0
        saw_comparable_numeric_fact = False

        for fact in relevant:
            fact_numbers = _normalize_numbers(fact.value) | _normalize_numbers(fact.raw_text)
            if claim_numbers and fact_numbers:
                saw_comparable_numeric_fact = True
                if claim_numbers & fact_numbers:
                    best_match, best_score = fact, 100.0
                    break
                continue
            score = max(_similarity(claim.claim_text, fact.value), _similarity(claim.claim_text, fact.raw_text))
            if score > best_score:
                best_match, best_score = fact, score

        if best_match is not None and best_score >= FUZZY_MATCH_THRESHOLD:
            status = VerificationStatus.VERIFIED
            explanation = f"Matched source fact {best_match.id} ({best_match.value!r}), similarity={best_score:.0f}"
            matched_ids = [best_match.id]
        elif relevant and (saw_comparable_numeric_fact or best_score > 0):
            status = VerificationStatus.CONTRADICTED
            explanation = "Claim's value does not match any linked source fact of a comparable type"
            matched_ids = []
        else:
            status = VerificationStatus.NOT_FOUND
            explanation = "No source fact of a comparable type is linked to this claim"
            matched_ids = []

        is_blocking = status != VerificationStatus.VERIFIED and claim.criticality in (Criticality.HIGH, Criticality.CRITICAL)
        return VerificationResult(
            project_id=claim.project_id,
            claim_id=claim.id,
            verification_type=VerificationType.DETERMINISTIC,
            status=status,
            matched_source_fact_ids=matched_ids,
            explanation=explanation,
            confidence=(best_score / 100.0) if best_match else 0.5,
            verifier_name="deterministic-regex-rapidfuzz",
            is_blocking=is_blocking,
        )

    def verify_semantic(self, claim: Claim, source_facts: list[SourceFact]) -> VerificationResult:
        relevant = _relevant_facts(claim, source_facts) or source_facts
        prompt = SEMANTIC_VERIFY_PROMPT.format(
            claim_text=claim.claim_text, facts_block=_render_facts_for_prompt(relevant[:20]),
        )
        try:
            raw = self.manager.generate_json(prompt, model=GEMINI_MODEL, temperature=0.0)
            if not isinstance(raw, dict):
                raise ValueError(f"expected a JSON object, got {type(raw)}")
            return _verification_result_from_json(raw, claim=claim, verifier_name=GEMINI_MODEL)
        except (GeminiAllKeysExhaustedError, ValueError, KeyError) as exc:
            logger.error("verify_semantic: falling back to UNCERTAIN for claim %s — %s", claim.id, exc)
            return VerificationResult(
                project_id=claim.project_id,
                claim_id=claim.id,
                verification_type=VerificationType.SEMANTIC,
                status=VerificationStatus.UNCERTAIN,
                matched_source_fact_ids=[],
                explanation=f"Semantic verification unavailable: {exc}",
                confidence=0.0,
                verifier_name=GEMINI_MODEL,
                is_blocking=claim.criticality in (Criticality.HIGH, Criticality.CRITICAL),
            )

    def verify_claim(self, claim: Claim, source_facts: list[SourceFact]) -> VerificationResult:
        if claim.claim_type.lower() in DETERMINISTIC_CLAIM_TYPES:
            return self.verify_deterministic(claim, source_facts)
        return self.verify_semantic(claim, source_facts)

    def verify_batch(self, claims: list[Claim], source_facts: list[SourceFact]) -> list[VerificationResult]:
        if not claims:
            return []

        deterministic_claims = [c for c in claims if c.claim_type.lower() in DETERMINISTIC_CLAIM_TYPES]
        semantic_claims = [c for c in claims if c.claim_type.lower() not in DETERMINISTIC_CLAIM_TYPES]

        results: dict[str, VerificationResult] = {c.id: self.verify_deterministic(c, source_facts) for c in deterministic_claims}
        if semantic_claims:
            results.update(self._verify_semantic_batch(semantic_claims, source_facts))

        return [results[c.id] for c in claims]

    def _verify_semantic_batch(self, claims: list[Claim], source_facts: list[SourceFact]) -> dict[str, VerificationResult]:
        """Batches every semantic claim into a single Gemini call —
        far cheaper on free-tier quota than one call per claim, and gives
        the key-rotation layer one call to retry/rotate instead of N."""
        prompt = SEMANTIC_VERIFY_BATCH_PROMPT.format(
            facts_block=_render_facts_for_prompt(source_facts[:40]),
            claims_block="\n".join(f"[{c.id}] {c.claim_text}" for c in claims),
        )

        try:
            raw = self.manager.generate_json(prompt, model=GEMINI_MODEL, temperature=0.0)
            if isinstance(raw, dict):
                raw = raw.get("results") or raw.get("verifications") or []
            by_id = {str(item["claim_id"]): item for item in raw} if isinstance(raw, list) else {}
        except (GeminiAllKeysExhaustedError, ValueError) as exc:
            logger.error("verify_batch: semantic batch call failed (%s) — falling back to per-claim calls", exc)
            return {c.id: self.verify_semantic(c, source_facts) for c in claims}

        out: dict[str, VerificationResult] = {}
        for claim in claims:
            item = by_id.get(claim.id)
            if item is None:
                logger.warning("verify_batch: no verdict for claim %s — falling back to a per-claim call", claim.id)
                out[claim.id] = self.verify_semantic(claim, source_facts)
                continue
            try:
                out[claim.id] = _verification_result_from_json(item, claim=claim, verifier_name=GEMINI_MODEL)
            except (ValueError, KeyError) as exc:
                logger.warning("verify_batch: malformed verdict for claim %s (%s) — falling back", claim.id, exc)
                out[claim.id] = self.verify_semantic(claim, source_facts)
        return out
