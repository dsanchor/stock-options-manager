# Decision: Auto-convert incomplete ROLL actions to CLOSE

**Author:** Rusty  
**Date:** 2026-07  
**Status:** Implemented  
**Commit:** 2086e07

## Context
Phase 2 agents sometimes output a ROLL type (e.g., ROLL_UP_AND_OUT) without selecting a specific candidate — `new_strike` and `new_expiration` are left null. This makes the activity unexecutable.

## Decision
Incomplete ROLL actions (missing `new_strike`, `new_expiration`, or `roll_economics`) are auto-converted to CLOSE with an audit trail appended to the reason field. This is consistent with the existing bare-ROLL → CLOSE conversion pattern.

## Rationale
- A ROLL without a target is worse than useless — it implies an action was chosen but can't be executed
- Converting to CLOSE is the safest fallback: it flags the position for manual review
- The audit trail in `reason` preserves what the agent originally recommended for debugging
- Instruction-level hardening reduces the frequency of this happening, but code validation is the safety net
