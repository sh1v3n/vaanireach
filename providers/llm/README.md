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
