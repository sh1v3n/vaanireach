# providers/llm/

LLM provider adapters (used by Script Agent, Fact Extraction Agent,
semantic verification, etc.) will live here. No vendor selected — see
[`docs/decisions/ADR-006-provider-selection.md`](../../docs/decisions/ADR-006-provider-selection.md).

Concrete adapters must satisfy the relevant `core.interfaces` contract
(e.g. `ScriptGenerator`, `FactExtractor`); nothing outside `providers/`
should import a vendor SDK directly.
