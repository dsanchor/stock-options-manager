# Decision: Monitor Instruction Split — Implementation Details

**Date:** 2026-07-22
**Author:** Linus (Quant Dev)
**Status:** Implemented — pending Rusty's runner integration
**Relates to:** danny-monitor-split.md

## What Was Done

Created 4 new instruction files per Danny's architecture decision, splitting each monitor agent into Assessment (Agent 1) + Roll Management (Agent 2).

## Design Decisions

### 1. Function-based exports (not module-level constants)
The new files use `get_open_call_assessment_instructions()` functions instead of `TV_OPEN_CALL_INSTRUCTIONS` constants. This allows future parameterization if needed (e.g., passing position-specific context into the prompt template).

### 2. Roll instructions import OPTIONS_CHAIN_SCHEMA_DESCRIPTION
Agent 2 files do `from src.options_chain_parser import OPTIONS_CHAIN_SCHEMA_DESCRIPTION` and inject it via f-string. This keeps the chain schema DRY — single source of truth in options_chain_parser.py.

### 3. Handoff schema includes roll_target_rules
Added a `roll_target_rules` field to the handoff JSON so Agent 2 can respect earnings-driven expiration constraints without needing the full earnings gate logic. Agent 1 pre-computes which expirations are blocked.

### 4. Profit optimization gate stays in Agent 1
Agent 1 evaluates the 3+4 gate conditions and reports `profit_optimization_gate: "passed"/"failed"/null`. Agent 2 trusts this and only handles the economics (finding the right strike, calculating net credit).

## Team Impact

- **Rusty**: Needs to wire up `agent_runner.py` to use the new instruction functions. The 2-phase flow: call `get_open_call_assessment_instructions()` for Agent 1, parse its output, and if non-WAIT, call `get_open_call_roll_instructions()` for Agent 2 with the handoff JSON + chain.
- **Basher**: Can test each agent independently — Agent 1 with mock position data, Agent 2 with mock handoff + chain.
- **Danny**: Review the handoff schema for completeness before Rusty integrates.

## Files

| File | Lines | Role |
|------|-------|------|
| `src/tv_open_call_assessment_instructions.py` | 463 | Call position assessment (Agent 1) |
| `src/tv_open_call_roll_instructions.py` | 298 | Call roll management (Agent 2) |
| `src/tv_open_put_assessment_instructions.py` | 462 | Put position assessment (Agent 1) |
| `src/tv_open_put_roll_instructions.py` | 300 | Put roll management (Agent 2) |
