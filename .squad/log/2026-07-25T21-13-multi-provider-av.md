# Session: 2026-07-25 Multi-Provider MCP Support + Alpha Vantage Instructions

**Date:** 2026-07-25T21:13:02Z  
**Focus:** Provider-agnostic configuration & dual instruction sets

## Completed Work

### Rusty (Agent Dev)
- ✅ Implemented multi-provider MCP config switching (massive + alphavantage)
- ✅ Added `_prune_inactive_providers()` to prevent env var substitution crashes
- ✅ Made `MCPStdioTool` name and env key dynamic via config
- ✅ Updated all 6 plumbing files (config.yaml, config.py, agent_runner.py, 2× agent files, main.py)
- ✅ Lazy imports in agent files prevent hard dependencies on missing instruction modules

### Linus (Quant Dev)
- ✅ Created `src/av_covered_call_instructions.py` (420 lines, AV tools pattern)
- ✅ Created `src/av_cash_secured_put_instructions.py` (569 lines, extended fundamentals)
- ✅ Kept strategy logic identical; only DATA GATHERING PROTOCOL differs
- ✅ Leveraged AV advantages: built-in technicals (RSI, BBANDS), EARNINGS data, sentiment scores

## Key Design Patterns

1. **Provider abstraction:** Single config file selects provider; all tool integration logic hidden from agents
2. **Lazy imports:** AV instruction files only loaded when AV provider selected; Massive mode unaffected
3. **Prune-before-substitute:** Inactive provider config removed before env var resolution
4. **Instruction parity:** Same decision criteria across providers; adaptations transparent

## Coordination

- Rusty's lazy import pattern enables Linus's parallel instruction development
- No blocking dependencies; both agents can run simultaneously
- Config file serves as single source of truth for provider selection

## Next Phase

1. **Basher** — Integration test: MCP server launch with both providers
2. **Danny** — E2E test: Agent decision quality with actual AV API
3. **Team** — Provider switching validation with real trading symbols
