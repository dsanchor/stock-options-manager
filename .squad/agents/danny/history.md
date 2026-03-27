# Danny — History

## Project Context
- **Project:** options-agent
- **User:** dsanchor
- **Stack:** Python, Microsoft Agent Framework, Azure Foundry (gpt-5.1)
- **MCP:** iflow-mcp_ferdousbhai_investor-agent 1.6.3
- **Description:** Two periodic trading agents for covered call and cash-secured put sell signals. Local runtime, configurable polling, stock symbols from files, decision logs, sell signal alerts.

## Learnings

### 2026-03-27: Model Configuration Updated to gpt-5.1

**User Directive (dsanchor):** Updated model from gpt-5.4-mini to gpt-5.1 in config/team.md

**Reason:** gpt-5.1 shows superior performance on multi-step TradingView Playwright workflows (navigate → click → snapshot sequences for options chain extraction). gpt-5.4-mini struggled with complex sequential browser instructions.

**Impact for Danny's Work:**
- Any downstream systems consuming agent outputs should verify compatibility with gpt-5.1 decision quality
- Model change applies to all providers (Massive.com, Alpha Vantage, TradingView) via team config inheritance
- Output format remains consistent (JSON+SUMMARY as per Rusty's 2026-03-27 update)
- No API contract changes, only model selection in config

**Status:** ✅ Updated in config/team.md
**Team:** User directive (dsanchor), Rusty (config implementation)
