# Orchestrator Agent

Sequences the pipeline: extract facts → detect critical facts → generate
script → verify → (if failed) regenerate → verify again → proceed. Owns
the `WorkflowRun` and emits `WorkflowEvent`s consumed by the future
dashboard's execution trace.

Will implement: [`core.interfaces.orchestrator.WorkflowEngine`](../../core/interfaces/orchestrator.py)

No logic implemented in Phase 0 — this package only reserves the import
namespace for Phase 1.
