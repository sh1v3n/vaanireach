# providers/llm/

`GroqLLMProvider` (`groq_provider.py`, backed by `groq_client.py`'s
`GroqManager`) — the LLM provider actually wired into the dashboard, and
the project's **permanent** LLM backend. Implements `FactExtractor`,
`ScriptGenerator`, `TranslationProvider`, and the semantic half of
`VerificationEngine` on top of Groq's OpenAI-compatible Chat Completions
API (`openai/gpt-oss-120b`), with the same horizontal-key-rotation
resilience shape (`itertools.cycle` + per-key cooldown) as every other
manager in this codebase. Deterministic verification
(`verify_deterministic`) is pure Python (regex + `rapidfuzz`) and never
touches the network regardless of which LLM backs the semantic half.

`GeminiLLMProvider` (`gemini_provider.py`, backed by `gemini_client.py`'s
`GeminiManager`) is kept in the codebase — correct, working — but no
longer wired in. Dropped because Gemini's free-tier daily quota (20
requests/day/key/model) proved far too tight for this pipeline's real
usage; Groq's free tier is roughly two orders of magnitude more generous
on this account and its LPU-based inference is consistently sub-second.
See
[`docs/decisions/ADR-006-provider-selection.md`](../../docs/decisions/ADR-006-provider-selection.md)
for the full story.

Both satisfy `ScriptGenerator`/`FactExtractor`/`TranslationProvider`/
`VerificationEngine` (in [`core/interfaces/`](../../core/interfaces/));
nothing outside `providers/` imports a vendor SDK/client directly.

## Reducing Groq TPM usage (2026-08-20)

Even a multi-key pool (`.env`'s `GROQ_API_KEYS`, comma-separated) got
rate-limited under sustained testing, since every call still shares the
same per-key 8,000 TPM ceiling. Three changes cut total usage rather than
just spreading it across more keys:

- **`_prioritized_facts()`** caps the fact ledger embedded in
  `generate_script_with_claims`'s prompt to `SCRIPT_GENERATION_FACTS_LIMIT`
  (40, matching `verify()`/`verify_batch()`'s existing caps) — this call
  runs once per target language, so an uncapped ledger was being resent
  in full 3× (EN/HI/MR). Keeps the highest-criticality facts first rather
  than truncating in extraction order, so capping doesn't risk dropping a
  critical deadline in favor of a low-priority background detail.
- **`EXTRACTION_CHUNK_CHARS`** raised 6000 → 9000: fewer, larger
  extraction chunks means fewer separate calls and less repeated
  per-call prompt-template overhead, now that the facts_block cap above
  reduces the downstream pressure that motivated the smaller original
  chunk size.
- `dashboard/app.py`'s `TARGET_LANGUAGES` temporarily drops Bengali (EN/HI/MR
  only) — one fewer full extraction+scripting+verification pass per
  document. One-line revert once headroom allows.
