# Decision: Runner 2-Phase Execution — Implementation Details

**Date:** 2026-07-22
**Author:** Rusty (Agent Dev)
**Status:** Implemented
**Implements:** danny-monitor-split.md

## What was done

Refactored `agent_runner.py` and both monitor wrappers to support the 2-phase Position Assessment → Roll Management execution model.

## Key design decisions

### 1. Backward-compatible opt-in via optional params
`run_position_monitor()` gained `assessment_instructions` and `roll_instructions` as optional kwargs. When both are `None`, the original single-agent path runs unchanged. This means the refactor is safe to merge even before Linus's instruction files land.

### 2. Handoff detection via `action_needed` key
Phase 1's output format diverges from the standard activity format:
- **WAIT path:** `{ "activity": "WAIT", ... }` → standard `_try_extract_json()` picks it up
- **Action path:** `{ "action_needed": "ROLL_UP_AND_OUT", ... }` → new `_try_extract_handoff_json()` picks it up

Using a distinct key (`action_needed` vs `activity`) avoids ambiguity and makes the detection reliable.

### 3. Phase 2 error resilience
If Phase 2 (roll management) fails for any reason, the runner persists Phase 1's handoff as a degraded activity with `roll_economics: null` and `"roll_agent_error"` appended to `risk_flags`. The run never crashes — the user sees the assessment result even if roll economics are unavailable.

### 4. Try/except import for parallel development
Monitor wrappers import Linus's instruction functions inside a try/except ImportError block. If the files don't exist yet, `assessment_instructions` and `roll_instructions` stay `None`, and the runner falls back to single-agent mode.

## Files changed
- `src/agent_runner.py` — new methods: `_try_extract_handoff_json`, `_run_position_assessment`, `_run_roll_management`; refactored `run_position_monitor`
- `src/open_call_monitor_agent.py` — imports + passes assessment/roll instructions
- `src/open_put_monitor_agent.py` — same pattern for puts

## Dependencies
- **Linus:** 4 instruction files must be committed for 2-phase mode to activate:
  - `src/tv_open_call_assessment_instructions.py`
  - `src/tv_open_call_roll_instructions.py`
  - `src/tv_open_put_assessment_instructions.py`
  - `src/tv_open_put_roll_instructions.py`
- Until those exist, the runner operates in single-agent fallback mode.
