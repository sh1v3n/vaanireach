# ADR-002: Source Fact Ledger as Single Source of Truth

**Status:** Accepted.

## Context

The core factual-preservation requirement is that generated content must
never invent or distort information from the source document, and
anything unverifiable must be flagged rather than fabricated. Multiple
pipeline stages (script generation, translation, storyboard) all need to
reference "the facts" — without a single authoritative structure, drift
between what the script says and what the document says becomes likely.

## Decision

`SourceFact` (`core/models/fact.py`) is the single source of truth for
the whole system:

- Every `SourceFact` carries a `SourceSpan` (document_id, page_number,
  text_span) — it can only originate from the document.
- Every `Claim` (script/translation output) must cite `source_fact_ids`.
- The script generator, translator, and storyboard planner all consume
  `SourceFact`s but never become authoritative themselves — the source
  document remains authoritative for the life of the project.
- `criticality` (LOW/MEDIUM/HIGH/CRITICAL) determines whether a failed
  verification on a fact-derived claim blocks publication.
- Deterministic verification is the designed path for objectively
  checkable `FactType`s: numbers, dates, amounts, percentages, names,
  URLs, phone numbers, locations, deadlines, scheme names.

## Consequences

- Any pipeline stage can be regenerated (a new script, a new translation)
  without re-deriving facts, because the ledger doesn't change.
- Verification always has a ground truth to check against, even for
  translated claims (verify against the same `SourceFact`s, not the
  English script).
