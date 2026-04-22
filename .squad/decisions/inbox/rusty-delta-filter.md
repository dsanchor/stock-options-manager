# Decision: Delta-based filtering on options chains

**Author:** Rusty (Agent Dev)
**Date:** 2026-07-22
**Status:** Implemented

## Context
Agents receive full options chains which include deep ITM/OTM contracts with extreme or missing deltas. These contracts are noise — agents rarely recommend them and they bloat the context window.

## Decision
Apply a delta filter in `_format_options_chain()` after position filtering but before JSON serialization:
- **Calls:** keep delta 0.15–0.90
- **Puts:** keep delta -0.60 to -0.15
- **Missing delta:** excluded

Default ranges are configurable via function parameters if future agents need different windows.

## Impact
- Reduces token usage in agent prompts (fewer contracts serialized)
- Agents focus on the most actionable strike range
- No behavioral change for agents that were already ignoring extreme-delta contracts
