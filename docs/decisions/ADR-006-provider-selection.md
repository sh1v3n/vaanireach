# ADR-006: LLM / Translation / TTS Provider Selection

**Status: DEFERRED pending benchmarking.**

## Context

Script generation, fact extraction (semantic parts), translation, and
text-to-speech each need a provider (an LLM API, a translation service, a
TTS vendor). Locking these in before comparing cost, latency, quality, and
— critically — Indian-language coverage would be premature.

## Decision

Defer selection for all of: LLM provider(s), `TranslationProvider`,
`TTSProvider`. Ship the interfaces now:

```python
# core/interfaces/translation_provider.py
class TranslationProvider(ABC):
    def supported_languages(self) -> list[LanguageCode]: ...
    def translate(self, text, source_language, target_language) -> str: ...
    def translate_claims(self, claims, target_language) -> list[Claim]: ...

# core/interfaces/tts_provider.py
class TTSProvider(ABC):
    def list_voices(self, language: LanguageCode) -> list[str]: ...
    def synthesize(self, text, language, voice_id=None) -> AudioAsset: ...
    def get_status(self, job_id: str) -> GenerationStatus: ...
```

LLM providers don't yet have a dedicated interface file since their
consumers (`ScriptGenerator`, `FactExtractor`, `VerificationEngine`'s
semantic path) already define the contract that matters — `providers/llm/`
is reserved for whichever concrete client library is chosen.

## Provider evaluation matrix (template — to be filled in during benchmarking)

| Provider | Cost | Latency | Language coverage (hi/mr/ta/…) | Quality | Offline-capable |
|---|---|---|---|---|---|
| _(candidate A)_ | | | | | |
| _(candidate B)_ | | | | | |

## Consequences

- `agents/script/`, `agents/translation/`, `agents/facts/`,
  `agents/verification/` can all be implemented against the interfaces
  above before any vendor decision is finalized.
- All API keys are read from environment variables only (`.env`, never
  committed) — see `.env.example`.
