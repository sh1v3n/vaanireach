# Script Agent

Calls `ScriptGenerator` to produce narration grounded in the Source Fact
Ledger, and calls `regenerate_script` when the Verification Agent reports
failures.

Will implement/consume: [`core.interfaces.script_generator.ScriptGenerator`](../../core/interfaces/script_generator.py)

No logic implemented in Phase 0 — this package only reserves the import
namespace for Phase 1.
