# Verification Agent

Runs `VerificationEngine.verify_batch` over a Script's/Translation's
claims, decides whether any CRITICAL claim is blocking, and signals the
Orchestrator to trigger regeneration when needed.

Will implement/consume: [`core.interfaces.verification_engine.VerificationEngine`](../../core/interfaces/verification_engine.py)

No logic implemented in Phase 0 — this package only reserves the import
namespace for Phase 1.
