# ADR-003: Agentic Orchestration Scope

**Status:** Accepted.

## Context

The spec calls for an "agentic multi-step workflow," but agents add real
complexity (state, tool selection, failure modes). Using an agent for
every trivial step would be over-engineering for a 24-hour hackathon.

## Decision

- Agents (`agents/*`) are created only where a genuine decision or tool
  selection is required: which document parser to use, which scene type
  fits a fact, whether to regenerate after a failed verification, etc.
- A single `WorkflowEngine` (`core/interfaces/orchestrator.py`) sequences
  agents synchronously — no distributed agent framework, no message
  queue, for the hackathon. This can change later if a real need for
  concurrency/distribution emerges.
- The orchestrator's control flow is: extract facts → detect critical
  facts → generate script → verify → (if failed) regenerate → verify
  again → proceed. This repeats per target language independently.
- Every stage transition emits a `WorkflowEvent`
  (`core/models/workflow.py`) with a concise, operational,
  user-facing `message`. **`WorkflowEvent.message` must never contain raw
  model reasoning or chain-of-thought** — only what a human reviewer
  needs to audit what happened and why (e.g. "Marathi verification
  failed — regeneration triggered").

## Consequences

- The dashboard can render a clean execution trace without any risk of
  leaking internal model reasoning.
- Adding a new agent later (e.g. a dedicated OCR-quality agent) doesn't
  require restructuring the orchestrator — it's just another stage in the
  same sequence.
