"""GroqLLMProvider — a single Groq-backed implementation of four Phase 0
interfaces: FactExtractor, ScriptGenerator, TranslationProvider, and (the
semantic half of) VerificationEngine. Deterministic verification is pure
Python (regex + rapidfuzz) and never touches the network, so it can never
be rate-limited and always runs even if every Groq key is dead.

Adopted in place of GeminiLLMProvider (providers/llm/gemini_provider.py,
kept in the codebase but no longer wired into the dashboard) — Gemini's
free-tier daily quota (20 requests/day/key/model) proved far too tight
for this pipeline's real usage, repeatedly exhausting all 3 configured
keys during live testing. See providers/llm/groq_client.py's module
docstring for the full rationale and model-choice verification.

The prompt templates and JSON -> domain-model conversion logic below are
intentionally near-identical to gemini_provider.py's — prompts are model-
agnostic, so this is a deliberate parallel port (matching this project's
established pattern of one file per provider rather than a shared-base
refactor), not independently reinvented. Diverge from gemini_provider.py
only where Groq's client shape requires it.

Every Groq call in this file goes through one shared GroqManager, so the
whole provider benefits from a single horizontal key-rotation pool (see
groq_client.py) — nothing here talks to the HTTP API directly.

Two documented, deliberate deviations from the strict Phase 0 interfaces
(both explained where they occur below, identical to gemini_provider.py):
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
from typing import Any

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
from providers.llm.groq_client import GroqAllKeysExhaustedError, GroqManager

try:
    from rapidfuzz import fuzz

    def _similarity(a: str, b: str) -> float:
        return float(fuzz.token_set_ratio(a, b))

except ImportError:  # pragma: no cover - only if rapidfuzz isn't installed
    from difflib import SequenceMatcher

    def _similarity(a: str, b: str) -> float:
        return SequenceMatcher(None, a.lower(), b.lower()).ratio() * 100.0


logger = logging.getLogger("vaanireach.providers.groq_provider")

GROQ_MODEL = "openai/gpt-oss-120b"  # see providers/llm/groq_client.py's DEFAULT_TEXT_MODEL docstring

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
# Identical to gemini_provider.py's — prompts are model-agnostic.

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

Structure it as a short STORY, not a list of facts, following this arc:
1. HOOK (the opening 1-2 sentences) — an attention-grabbing line that makes the viewer want to \
keep watching: a relatable question, a striking number, or a direct address to the viewer. This \
exact opening becomes the video's first 5 spoken seconds on screen, so it must stand alone and \
immediately signal what this is about — never start with a dry "This notice concerns..." opener.
2. CONTEXT — one or two sentences on why this matters to {audience}.
3. THE KEY FACTS — the concrete, checkable details (amounts, dates, eligibility, how to apply), \
woven naturally into the story rather than listed like a form.
4. CALL TO ACTION — a short, clear closing line telling the viewer exactly what to do next.

Ground every sentence ONLY in the facts below — never invent a date, amount, name, or \
number that isn't listed. Aim for roughly {target_words} words (natural spoken pace). Write \
warmly and conversationally, like a trusted person telling you something useful — not like a \
legal notice.

SOURCE FACTS (id, type, criticality, value, raw text):
{facts_block}

ORIGINAL DOCUMENT CONTEXT (for tone/context only — facts above are authoritative):
{source_context}

Return ONLY a JSON object with exactly these keys:
- "narration_text": the full narration script, in {language}, as one string, following the \
HOOK -> CONTEXT -> KEY FACTS -> CALL TO ACTION arc above
- "claims": an array of every checkable statement the narration makes, each an object with:
  - "claim_text": the exact sentence/phrase from narration_text
  - "claim_type": a short free-form label, e.g. "amount", "deadline", "eligibility", "location"
  - "criticality": "low" | "medium" | "high" | "critical"
  - "source_fact_ids": array of the fact ids (from the bracketed [id] above) this claim is \
grounded in

No markdown fences, no commentary — the raw JSON object only.
"""

# Combined variant of SCRIPT_GENERATION_PROMPT above, used by
# generate_scripts_with_claims_multi() to produce every target language's
# script in ONE call instead of one call per language — see that method's
# docstring and providers/llm/README.md's "Reducing Groq TPM usage"
# section for why. Same HOOK -> CONTEXT -> KEY FACTS -> CALL TO ACTION
# structure and grounding rules per language, just requested for all of
# them against one shared copy of the facts/context instead of N copies.
MULTI_LANGUAGE_SCRIPT_GENERATION_PROMPT = """You are writing a {duration}-second spoken-narration \
video script for an Indian government outreach video, aimed at: {audience}, in EACH of these \
languages: {languages_list}.

Structure EACH language's script as a short STORY, not a list of facts, following this arc:
1. HOOK (the opening 1-2 sentences) — an attention-grabbing line that makes the viewer want to \
keep watching: a relatable question, a striking number, or a direct address to the viewer. This \
exact opening becomes the video's first 5 spoken seconds on screen, so it must stand alone and \
immediately signal what this is about — never start with a dry "This notice concerns..." opener.
2. CONTEXT — one or two sentences on why this matters to {audience}.
3. THE KEY FACTS — the concrete, checkable details (amounts, dates, eligibility, how to apply), \
woven naturally into the story rather than listed like a form.
4. CALL TO ACTION — a short, clear closing line telling the viewer exactly what to do next.

Ground every sentence ONLY in the facts below — never invent a date, amount, name, or number \
that isn't listed. Aim for roughly {target_words} words per language (natural spoken pace). Write \
warmly and conversationally, like a trusted person telling you something useful — not like a \
legal notice. Write each language's script as an ORIGINAL composition in that language (natural, \
idiomatic phrasing) — NOT a mechanical translation of one master script; each should independently \
follow the arc above and may vary in structure/phrasing from the others.

SOURCE FACTS (id, type, criticality, value, raw text):
{facts_block}

ORIGINAL DOCUMENT CONTEXT (for tone/context only — facts above are authoritative):
{source_context}

Return ONLY a JSON object with exactly one key per language code ({language_codes_list}), each \
value an object with exactly these keys:
- "narration_text": the full narration script in THAT language, as one string, following the \
HOOK -> CONTEXT -> KEY FACTS -> CALL TO ACTION arc above
- "claims": an array of every checkable statement that language's narration makes, each an object with:
  - "claim_text": the exact sentence/phrase from that language's narration_text
  - "claim_type": a short free-form label, e.g. "amount", "deadline", "eligibility", "location"
  - "criticality": "low" | "medium" | "high" | "critical"
  - "source_fact_ids": array of the fact ids (from the bracketed [id] above) this claim is \
grounded in

No markdown fences, no commentary — the raw JSON object only, with all {num_languages} language \
keys present.
"""

SCRIPT_REGENERATION_PROMPT = """Revise the {duration}-second {language} narration script \
below for audience: {audience}. Verification found problems — fix ONLY what's listed, \
keep everything else (including its HOOK -> CONTEXT -> KEY FACTS -> CALL TO ACTION story \
structure and warm, conversational tone) as close to the original as possible.

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

# Groq's shared per-key budget is a hard 8,000 tokens/minute (see
# groq_client.py's module docstring) — a single fact-extraction call over a
# large document can burn nearly all of it on input alone, leaving too
# little for the JSON output and causing it to get cut off mid-generation
# (a 400 json_validate_failed with an empty failed_generation, verified
# live against a ~24KB real circular: one call consumed 7,729/8,000 TPM on
# input, then failed). extract_facts() below splits the document into
# EXTRACTION_CHUNK_CHARS-sized pieces and extracts facts chunk-by-chunk
# instead of in one call, so each individual request stays a small
# fraction of the budget regardless of total document size.
EXTRACTION_CHUNK_CHARS = 9000  # ~2,250 tokens of input per chunk — raised from
# 6000 on 2026-08-20 (fewer, larger chunks = fewer separate calls = less
# repeated per-call template overhead) once facts_block was also capped
# below, which reduced the pressure that motivated the smaller original
# size in the first place. Still comfortably clear of the 8,000 TPM
# ceiling on any single key.
EXTRACTION_CHUNK_OVERLAP_CHARS = 200  # small overlap so a fact split across
# a chunk boundary still appears whole in at least one chunk


def _chunk_text(text: str, *, chunk_chars: int, overlap_chars: int) -> list[str]:
    """Splits `text` into overlapping chunks, preferring to break on a
    paragraph boundary (blank line) near the target size rather than
    mid-sentence, so a single fact is unlikely to be sliced in half."""
    if len(text) <= chunk_chars:
        return [text]

    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_chars, n)
        if end < n:
            break_at = text.rfind("\n\n", start, end)
            if break_at > start + chunk_chars // 2:  # don't shrink a chunk to near-nothing
                end = break_at
        chunks.append(text[start:end])
        if end >= n:
            break
        start = max(end - overlap_chars, start + 1)
    return chunks


def _iter_extraction_chunks(pages: list[DocumentPage]) -> list[str]:
    """One prompt-ready chunk per page-chunk, each carrying its own
    "--- Page N ---" header so a fact's page_number stays correct even
    when a single long page is split across multiple extraction calls."""
    chunks: list[str] = []
    for page in pages:
        for piece in _chunk_text(
            page.raw_text, chunk_chars=EXTRACTION_CHUNK_CHARS, overlap_chars=EXTRACTION_CHUNK_OVERLAP_CHARS
        ):
            chunks.append(f"--- Page {page.page_number} ---\n{piece}")
    return chunks


# generate_script_with_claims embeds the fact ledger once PER LANGUAGE
# (3 calls for EN/HI/MR — see dashboard/app.py's TARGET_LANGUAGES), so an
# uncapped ledger gets resent in full that many times, each one competing
# for the same shared Groq TPM budget. verify()/verify_batch() already cap
# theirs (facts[:20]/facts[:40] respectively) — this brings script
# generation in line with that instead of being the one uncapped site.
SCRIPT_GENERATION_FACTS_LIMIT = 40
_CRITICALITY_RANK = {Criticality.CRITICAL: 0, Criticality.HIGH: 1, Criticality.MEDIUM: 2, Criticality.LOW: 3}


def _prioritized_facts(facts: list[SourceFact], limit: int) -> list[SourceFact]:
    """Caps `facts` to `limit` entries, keeping the highest-criticality
    (then highest-confidence) facts rather than truncating in whatever
    order extraction happened to return them — so capping the prompt
    size doesn't silently prefer dropping a critical deadline over a
    low-priority background detail just because of extraction order."""
    if len(facts) <= limit:
        return facts
    ordered = sorted(facts, key=lambda f: (_CRITICALITY_RANK.get(f.criticality, 4), -f.confidence))
    return ordered[:limit]


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
        extractor_name=GROQ_MODEL,
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


def _script_and_claims_from_json(
    raw: dict,
    *,
    project_id: str,
    target_language: LanguageCode,
    audience: str,
    desired_duration_seconds: int,
    source_facts: list[SourceFact],
) -> tuple[Script, list[Claim]]:
    """Shared Script/Claim construction for one language's
    {"narration_text", "claims"} entry — used by both
    generate_script_with_claims() (one entry, its own call) and
    generate_scripts_with_claims_multi() (one entry per language, sliced
    out of one combined call's response), so the two stay in lockstep
    rather than drifting into two slightly-different implementations."""
    script = Script(
        project_id=project_id,
        language=target_language,
        audience=audience,
        target_duration_seconds=desired_duration_seconds,
        narration_text=str(raw.get("narration_text", "")).strip(),
        source_fact_ids=[f.id for f in source_facts],
        generator_name=GROQ_MODEL,
        version=1,
        status=ScriptStatus.DRAFT,
    )

    claims: list[Claim] = []
    for item in raw.get("claims", []) if isinstance(raw.get("claims"), list) else []:
        try:
            claims.append(_claim_from_json(item, project_id=project_id, script_id=script.id, language=target_language))
        except Exception as exc:  # noqa: BLE001 - one malformed claim must not drop the rest
            logger.warning("_script_and_claims_from_json: skipping malformed claim %r: %s", item, exc)

    script.claim_ids = [c.id for c in claims]
    return script, claims


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

class GroqLLMProvider(FactExtractor, ScriptGenerator, TranslationProvider, VerificationEngine):
    def __init__(self, manager: GroqManager | None = None) -> None:
        self.manager = manager or GroqManager()

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
        chunks = _iter_extraction_chunks(pages)

        facts: list[SourceFact] = []
        seen: set[tuple[str, str]] = set()
        chunks_ok = 0
        for i, chunk_text in enumerate(chunks):
            prompt = FACT_EXTRACTION_PROMPT.format(fact_types=fact_types, document_text=chunk_text)
            try:
                raw = self.manager.generate_json(prompt, model=GROQ_MODEL, temperature=0.1)
            except GroqAllKeysExhaustedError as exc:
                # One exhausted chunk shouldn't discard facts already
                # extracted from the others — log and keep going.
                logger.warning(
                    "extract_facts: chunk %d/%d exhausted Groq keys, skipping (%s)", i + 1, len(chunks), exc
                )
                continue
            chunks_ok += 1

            if isinstance(raw, dict):
                raw = raw.get("facts", [])
            items = raw if isinstance(raw, list) else []

            for item in items:
                try:
                    fact = _fact_from_json(item, document_id=document_id, project_id=project_id)
                except Exception as exc:  # noqa: BLE001 - one malformed fact must not drop the rest
                    logger.warning("extract_facts: skipping malformed fact %r: %s", item, exc)
                    continue
                # The chunk overlap can surface the same fact twice — collapse it
                # by (type, normalized value) so the ledger stays de-duplicated.
                dedupe_key = (fact.fact_type.value, fact.value.strip().lower())
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                facts.append(fact)

        if chunks_ok == 0:
            logger.error(
                "extract_facts: all %d chunk(s) exhausted Groq keys — returning empty fact ledger", len(chunks)
            )
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
            facts_block=_render_facts_for_prompt(_prioritized_facts(source_facts, SCRIPT_GENERATION_FACTS_LIMIT)),
            source_context=(source_context or "")[:4000],
        )

        raw = self.manager.generate_json(prompt, model=GROQ_MODEL, temperature=0.5)
        if not isinstance(raw, dict):
            logger.warning("generate_script: expected a JSON object, got %s", type(raw))
            raw = {}

        return _script_and_claims_from_json(
            raw, project_id=project_id, target_language=target_language, audience=audience,
            desired_duration_seconds=desired_duration_seconds, source_facts=source_facts,
        )

    def generate_scripts_with_claims_multi(
        self,
        source_facts: list[SourceFact],
        source_context: str,
        target_languages: list[LanguageCode],
        audience: str,
        desired_duration_seconds: int,
        *,
        project_id: str,
    ) -> dict[LanguageCode, tuple[Script, list[Claim]]]:
        """Generates every language's narration+claims in ONE Groq call
        instead of one call per language (what `generate_script_with_claims`
        does) — the single biggest lever for reducing this pipeline's Groq
        TPM usage (see providers/llm/README.md): the fact ledger +
        source-document-context + prompt-template overhead was being
        resent in full once per language, all competing for the same
        shared per-key budget (groq_client.py's module docstring); this
        sends it once regardless of how many languages are requested.

        Not a hard all-or-nothing call: if the combined response is
        missing a language (a partial/malformed JSON completion, or the
        combined call failing outright) that language falls back to its
        own individual `generate_script_with_claims` call rather than the
        whole batch failing — a rare degraded case shouldn't cost every
        other language its script too."""
        if not target_languages:
            return {}

        target_words = max(40, int(desired_duration_seconds * 2.3))
        language_codes = [lang.value for lang in target_languages]
        prompt = MULTI_LANGUAGE_SCRIPT_GENERATION_PROMPT.format(
            audience=audience,
            duration=desired_duration_seconds,
            target_words=target_words,
            languages_list=", ".join(language_codes),
            language_codes_list=", ".join(f'"{code}"' for code in language_codes),
            num_languages=len(language_codes),
            facts_block=_render_facts_for_prompt(_prioritized_facts(source_facts, SCRIPT_GENERATION_FACTS_LIMIT)),
            source_context=(source_context or "")[:4000],
        )

        raw: Any = None
        try:
            raw = self.manager.generate_json(prompt, model=GROQ_MODEL, temperature=0.5)
        except (GroqAllKeysExhaustedError, ValueError) as exc:
            logger.warning(
                "generate_scripts_with_claims_multi: combined call failed (%s) — every language "
                "will fall back to an individual call", exc,
            )

        results: dict[LanguageCode, tuple[Script, list[Claim]]] = {}
        if isinstance(raw, dict):
            for lang in target_languages:
                entry = raw.get(lang.value)
                if not isinstance(entry, dict) or not str(entry.get("narration_text", "")).strip():
                    continue
                results[lang] = _script_and_claims_from_json(
                    entry, project_id=project_id, target_language=lang, audience=audience,
                    desired_duration_seconds=desired_duration_seconds, source_facts=source_facts,
                )

        missing = [lang for lang in target_languages if lang not in results]
        if missing:
            logger.warning(
                "generate_scripts_with_claims_multi: %d/%d language(s) missing from the combined "
                "response (%s) — generating them individually",
                len(missing), len(target_languages), ", ".join(l.value for l in missing),
            )
            for lang in missing:
                results[lang] = self.generate_script_with_claims(
                    source_facts, source_context, lang, audience, desired_duration_seconds, project_id=project_id,
                )

        return results

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
            raw = self.manager.generate_json(prompt, model=GROQ_MODEL, temperature=0.5)
        except GroqAllKeysExhaustedError:
            logger.error("regenerate_script: all Groq keys exhausted — keeping previous script unchanged")
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
            generator_name=GROQ_MODEL,
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
            return self.manager.generate_text(prompt, model=GROQ_MODEL, temperature=0.3).strip()
        except GroqAllKeysExhaustedError:
            logger.error("translate: all Groq keys exhausted — returning source text unchanged")
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
            raw = self.manager.generate_json(prompt, model=GROQ_MODEL, temperature=0.3)
            if isinstance(raw, list) and len(raw) == len(pending):
                translated_by_id = {c.id: str(t) for c, t in zip(pending, raw)}
            else:
                logger.warning("translate_claims: batch response shape mismatch — falling back to per-claim calls")
        except (GroqAllKeysExhaustedError, ValueError) as exc:
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
            raw = self.manager.generate_json(prompt, model=GROQ_MODEL, temperature=0.0)
            if not isinstance(raw, dict):
                raise ValueError(f"expected a JSON object, got {type(raw)}")
            return _verification_result_from_json(raw, claim=claim, verifier_name=GROQ_MODEL)
        except (GroqAllKeysExhaustedError, ValueError, KeyError) as exc:
            logger.error("verify_semantic: falling back to UNCERTAIN for claim %s — %s", claim.id, exc)
            return VerificationResult(
                project_id=claim.project_id,
                claim_id=claim.id,
                verification_type=VerificationType.SEMANTIC,
                status=VerificationStatus.UNCERTAIN,
                matched_source_fact_ids=[],
                explanation=f"Semantic verification unavailable: {exc}",
                confidence=0.0,
                verifier_name=GROQ_MODEL,
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
        """Batches every semantic claim into a single Groq call — one
        request instead of N, and gives the key-rotation layer one call
        to retry/rotate instead of N."""
        prompt = SEMANTIC_VERIFY_BATCH_PROMPT.format(
            facts_block=_render_facts_for_prompt(source_facts[:40]),
            claims_block="\n".join(f"[{c.id}] {c.claim_text}" for c in claims),
        )

        try:
            raw = self.manager.generate_json(prompt, model=GROQ_MODEL, temperature=0.0)
            if isinstance(raw, dict):
                raw = raw.get("results") or raw.get("verifications") or []
            by_id = {str(item["claim_id"]): item for item in raw} if isinstance(raw, list) else {}
        except (GroqAllKeysExhaustedError, ValueError) as exc:
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
                out[claim.id] = _verification_result_from_json(item, claim=claim, verifier_name=GROQ_MODEL)
            except (ValueError, KeyError) as exc:
                logger.warning("verify_batch: malformed verdict for claim %s (%s) — falling back", claim.id, exc)
                out[claim.id] = self.verify_semantic(claim, source_facts)
        return out
