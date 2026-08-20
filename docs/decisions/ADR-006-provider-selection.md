# ADR-006: LLM / Translation / TTS Provider Selection

**Status: DECIDED (Phase 1-2).**

## Context

Script generation, fact extraction (semantic parts), translation, and
text-to-speech each needed a provider (an LLM API, a translation service,
a TTS vendor). Locking these in before comparing cost, latency, quality,
and — critically — Indian-language coverage would have been premature.

## Decision

**LLM (fact extraction, script generation, translation, semantic
verification): Google Gemini** (`gemini-3.6-flash`), via the
`google-genai` SDK. One shared resilience primitive,
[`providers/llm/gemini_client.py`](../../providers/llm/gemini_client.py)'s
`GeminiManager`, backs everything: horizontal rotation across a pool of
`GEMINI_API_KEYS` (`itertools.cycle` + a per-key cooldown on
rate-limit/auth errors, short exponential backoff on transient 5xx
errors), raising `GeminiAllKeysExhaustedError` only once a full rotation
has failed. `GeminiLLMProvider`
([`providers/llm/gemini_provider.py`](../../providers/llm/gemini_provider.py))
implements `FactExtractor`, `ScriptGenerator`, `TranslationProvider`, and
the semantic half of `VerificationEngine` on top of it — deterministic
verification (`verify_deterministic`) is pure Python (regex +
`rapidfuzz`), never touches the network, and so is never affected by key
exhaustion. `GeminiImagenProvider` (`providers/visual/gemini_imagen_provider.py`)
originally reused this same `GeminiManager` instance for image
generation too, sharing one key-rotation pool across text and image
calls — since superseded by `HuggingFaceVisualProvider` for B-roll/avatar
images specifically (Gemini Imagen needs a billing-enabled Google Cloud
project even on free-tier API keys; see ADR-004).

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
  (`dashboard/app.py`) calls `GeminiLLMProvider` directly rather than
  through an agent layer, per the Phase 5 hackathon scope.
- All API keys are read from environment variables only (`.env`, never
  committed) — see `.env.example`. A single `GEMINI_API_KEY` /
  `HEDRA_API_KEY` / etc. is also accepted as a 1-key pool for anyone who
  only has one key.
- Indian-language coverage: Sarvam's `bn/gu/hi/kn/ml/mr/ta/te` BCP-47
  codes cover 8 of the project's 9 `LanguageCode`s directly; `edge-tts`'s
  Neural voices cover the same 8 as the fallback tier.
