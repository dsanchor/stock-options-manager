# Decision: Remove Legacy Single-Agent Fallback from Position Monitors

**Date:** 2026-07  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  

## Context
The 2-phase position monitor flow (Phase 1: assessment, Phase 2: roll management) was introduced with a try/except ImportError fallback so the code could merge before Linus committed the instruction files. Both call and put instruction files are now committed and stable.

## Decision
Remove all legacy single-agent fallback paths:
- `open_call_monitor_agent.py` / `open_put_monitor_agent.py`: Direct imports instead of try/except; drop `instructions=` parameter from `run_position_monitor` calls.
- `agent_runner.py`: Drop `instructions` parameter from `run_position_monitor` signature; remove `two_phase` boolean check; delete the ~50-line single-agent `else` branch; hard-code `two_phase: True` in telemetry.

## Rationale
- Dead code: the single-agent path was unreachable since the instruction files are committed.
- Simpler control flow: one execution path instead of two branching conditionally.
- Prevents accidental regression to the less capable single-agent mode.
- Net deletion of ~80 lines.

## Impact
- No runtime behavior change — the 2-phase path was already the only path executed.
- Any future instruction file changes must keep the assessment/roll module pattern.
