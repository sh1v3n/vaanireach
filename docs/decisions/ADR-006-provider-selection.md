# ADR-006: LLM / Translation / TTS Provider Selection

**Status: DECIDED (Phase 1-2); LLM backend revised 2026-08-20
(Gemini → Groq) — see the revision note below.**

## Context

Script generation, fact extraction (semantic parts), translation, and
text-to-speech each needed a provider (an LLM API, a translation service,
a TTS vendor). Locking these in before comparing cost, latency, quality,
and — critically — Indian-language coverage would have been premature.

## Decision

**LLM (fact extraction, script generation, translation, semantic
verification): Groq** (`openai/gpt-oss-120b`), via its OpenAI-compatible
Chat Completions REST API. One shared resilience primitive,
[`providers/llm/groq_client.py`](../../providers/llm/groq_client.py)'s
`GroqManager`, backs everything: horizontal rotation across a pool of
`GROQ_API_KEYS` (`itertools.cycle` + a per-key cooldown on
rate-limit/auth errors, short exponential backoff on transient 5xx
errors), raising `GroqAllKeysExhaustedError` only once a full rotation
has failed — the identical shape as the original `GeminiManager`.
`GroqLLMProvider`
([`providers/llm/groq_provider.py`](../../providers/llm/groq_provider.py))
implements `FactExtractor`, `ScriptGenerator`, `TranslationProvider`, and
the semantic half of `VerificationEngine` on top of it, using the exact
same prompt templates as the original Gemini implementation (prompts are
model-agnostic) — deterministic verification (`verify_deterministic`) is
pure Python (regex + `rapidfuzz`), never touches the network, and so is
never affected by key exhaustion regardless of which LLM backs the
semantic half.

**Revision (2026-08-20): LLM backend moved off Gemini, onto Groq.**
`GeminiLLMProvider`/`GeminiManager` (`providers/llm/gemini_provider.py`,
`gemini_client.py`) are kept in the codebase — correct, working — but no
longer wired into the dashboard. Reason: Gemini's free-tier daily quota
(`GenerateRequestsPerDayPerProjectPerModel-FreeTier`, 20 requests/day per
key per model) proved far too tight for this pipeline's real usage — all
3 configured Gemini keys were repeatedly exhausted during live testing,
each time blocking fact extraction entirely with no fallback (there is
none for the core LLM role, unlike TTS/avatar/images). Confirmed live
before switching: Groq's free tier allows 1000 requests / 8000 tokens
per short rolling window on this account — roughly two orders of
magnitude more headroom — with sub-second latency (LPU-based inference)
and fluent, accurate output in both English and Indian languages
(spot-checked Hindi live, not assumed). `GeminiImagenProvider`
(`providers/visual/gemini_imagen_provider.py`) previously reused
`GeminiManager` for image generation too, sharing one key-rotation pool
across text and image calls — B-roll/avatar images have since moved to
Cloudflare Workers AI regardless (see ADR-004), so this pool-sharing no
longer applies either way.

**TTS: Sarvam AI, with a hard vertical failover to `edge-tts`.**
[`providers/tts/sarvam_tts_provider.py`](../../providers/tts/sarvam_tts_provider.py)'s
`SarvamTTSProvider` tries `SarvamTTSManager` (horizontal rotation across
`SARVAM_API_KEYS`) first; on pool exhaustion, a rejected request, or any
other hard failure, it drops to `edge-tts` — free, local, no API key
required — so narration audio is always produced even with zero working
Sarvam keys. The same module owns `slice_hook_and_body()`, which splits
one synthesized track into the 5-second avatar "hook" clip and the
remaining "body" track for the B-roll voiceover.

```python
# core/interfaces/translation_provider.py — unchanged from the original design
class TranslationProvider(ABC):
    def supported_languages(self) -> list[LanguageCode]: ...
    def translate(self, text, source_language, target_language) -> str: ...
    def translate_claims(self, claims, target_language) -> list[Claim]: ...

# core/interfaces/tts_provider.py — unchanged from the original design
class TTSProvider(ABC):
    def list_voices(self, language: LanguageCode) -> list[str]: ...
    def synthesize(self, text, language, voice_id=None) -> AudioAsset: ...
    def get_status(self, job_id: str) -> GenerationStatus: ...
```

Both concrete providers document one small, deliberate gap from the
strict ABC signatures above: `synthesize()`/`extract_facts()`/
`generate_script()` etc. all gain a required keyword-only `project_id`
argument, since the models they construct require it but the original
ABC signatures didn't pass one. Python's ABC machinery only checks that a
method name is overridden, not its exact signature, so this is safe and
is called out in each provider's module docstring.

## Consequences

- `agents/script/`, `agents/translation/`, `agents/facts/`,
  `agents/verification/` remain unimplemented — the dashboard
  (`dashboard/app.py`) calls `GroqLLMProvider` directly rather than
  through an agent layer, per the Phase 5 hackathon scope.
- All API keys are read from environment variables only (`.env`, never
  committed) — see `.env.example`. A single `GEMINI_API_KEY` /
  `HEDRA_API_KEY` / etc. is also accepted as a 1-key pool for anyone who
  only has one key.
- Indian-language coverage: Sarvam's `bn/gu/hi/kn/ml/mr/ta/te` BCP-47
  codes cover 8 of the project's 9 `LanguageCode`s directly; `edge-tts`'s
  Neural voices cover the same 8 as the fallback tier.
