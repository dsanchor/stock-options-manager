# Decision: Roll Cost Sign Convention + Profit Optimization Gate Split

**Author:** Linus (Quant Dev)
**Date:** 2026-07
**Status:** Implemented
**Files:** tv_open_call_roll_instructions.py, tv_open_put_roll_instructions.py, tv_open_call_assessment_instructions.py, tv_open_put_assessment_instructions.py

## Context
Rubber duck review of the monitor agent split found 3 issues in the instruction files.

## Decisions

### 1. estimated_roll_cost = new_premium - buyback_cost (always)
Examples in both roll files showed negative roll cost alongside positive net credit — contradictory. Fixed all examples so `estimated_roll_cost` equals the net credit/debit math. Positive = credit, negative = debit, consistent with the rules text.

### 2. Profit optimization gate: "eligible" (not "passed")
Agent 1 (assessment) was checking conditions it cannot evaluate — specifically "no earnings/ex-div before new expiration" — because Agent 2 (roll management) selects the expiration. Changed:
- Gate result from "passed" → "eligible" (Agent 1's checks passed, but Agent 2 must validate)
- Removed 2 candidate-dependent flexible conditions from assessment; now 5 stock-level conditions, need 3 of 5
- Added `profit_optimization_constraints` to handoff JSON so Agent 2 gets earnings/ex-div dates
- Added PROFIT OPTIMIZATION VALIDATION section to both roll files

### 3. Mandatory JSON output in roll agents
Added explicit warning: roll agents MUST always produce a JSON activity block. If no viable roll, output CLOSE with `roll_tier: "no_viable_roll"`.

## Team Impact
- **Rusty**: agent_runner.py should handle "eligible" in addition to "passed" if it inspects `profit_optimization_gate`. The null JSON issue (Finding 3) needs a framework-level guard in agent_runner.py too — instructions alone aren't sufficient.
- **Danny**: No direct frontend impact, but the `profit_optimization_gate` value in decision logs will now show "eligible" instead of "passed" for profit optimization rolls.
