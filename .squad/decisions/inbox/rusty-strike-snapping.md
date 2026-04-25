# Decision: Pivot Points Are Guidance, Not Literal Strike Values

**Author:** Rusty  
**Date:** 2026-07  
**Status:** Applied  

## Context
Phase 2 roll management treated pivot point levels (R1/R2/R3 for calls, S1/S2/S3 for puts) as literal strike prices to look up in the candidates table. These calculated values almost never match actual option chain strikes, causing failed lookups and unnecessary CLOSE recommendations.

## Decision
- Pivot points and delta targets are **guidance for choosing among actual table rows**, not literal strike values.
- When a target falls between available strikes, snap in the safe direction: **UP for calls, DOWN for puts**.
- The agent must ONLY select strikes that exist as rows in the candidates table.
- The ROLL SEARCH ALGORITHM references "next available strike(s)" instead of fixed dollar offsets.

## Impact
Both `tv_open_call_roll_instructions.py` and `tv_open_put_roll_instructions.py` updated. No code changes needed — this is instruction-level guidance that the LLM agent follows at runtime.
