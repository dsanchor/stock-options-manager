# Rusty — History

## Project Context
- **Project:** options-agent
- **User:** dsanchor
- **Stack:** Python, Microsoft Agent Framework, Azure Foundry (gpt-5.1)
- **MCP:** iflow-mcp_ferdousbhai_investor-agent 1.6.3
- **Description:** Two periodic trading agents for covered call and cash-secured put sell signals. Local runtime, configurable polling, stock symbols from files, decision logs, sell signal alerts.

## Core Context

**Consolidated work items from March–July 2026:**
- **Phase 1–4a (CosmosDB Refactor):** Completed CosmosDB foundation, scheduler refactor, web dashboard migration, phase completion
- **TradingView Data Layer:** Pre-fetch architecture with Playwright for options, BS4+scanner API for overview/technicals/forecast/dividends (5 test scripts + tv_data_fetcher.py refactor)
- **Agent Infrastructure:** Added telemetry, telegram notifications per-symbol, settings container, manual roll endpoint, context overflow handling
- **Dashboard & API:** Full REST API, symbol detail pages, position management, settings persistence via CosmosDB
- **Key Patterns:** Dict-spread protection (reassert controlled fields), lazy Playwright, multi-strategy fallback (HTML → JSON → API), scan error handling

**Recent key fixes & decisions:**
- Timestamp consistency (2025-07): Reassert `doc["timestamp"]` after `**spread` in write_activity/alert
- Config precedence (2026-03-31): Merge CosmosDB settings into Config at runtime
- Per-symbol notification toggles (2026-03-31): `telegram_notifications_enabled` field
- Playwright locator refactor (2026-03-31): Targeted "Fundamentals and stats" extraction
- JSON format hints (2026-03-31): Added parenthetical notes to 4 instruction files

## Tasks

### Alert Visibility Fix + Display Reorder (2026-03-31)
**Status:** ⏳ In Progress  
**Agent:** Rusty (spawned background)  
**Subtasks:**
1. cosmos_db.py: Protect `doc_type` from `**data` spread override in write_activity() and write_alert()
2. symbol_detail.html: Move alerts section before activities section
3. Test and verify changes
4. Commit with signed message

### JSON Format Hints (2026-03-31T09:30Z)
**Status:** ✅ Completed  
**Commit:** 3756071  
**Files:** 4 instruction files  
- Added parenthetical JSON format notes to agent instructions for improved LLM clarity
- Modified: `src/tv_covered_call_instructions.py`, `src/tv_open_call_instructions.py`, `src/tv_open_put_instructions.py`, `src/tv_cash_secured_put_instructions.py`

## Learnings

### Dict-Spread Protection Pattern
When using `**spread` in Python dict literals, always reassert controlled fields AFTER the spread. Never rely on key ordering alone. Pattern: `doc["field"] = value` after `{**data}`.

### Lazy Initialization of Expensive Resources
Playwright + Chromium are expensive. Initialize lazily via helper method (`_ensure_browser()`) rather than in `__init__`. Saves resources when only lightweight fetchers (BS4) run.

### Multi-Strategy Data Extraction
Implement 3-level fallback: (1) targeted HTML extraction, (2) embedded JSON parsing, (3) API fallback. Each strategy provides value-add error handling and graceful degradation.

### TradingView Scanner API for Validation
The unauthenticated `/america/scan` endpoint (not `/options/scan2`) provides fundamentals, technicals, forecast, and dividends data without browser context. Returns "Unknown field" for invalid columns.

### Test Scripts as Documentation
Standalone test scripts (test_fetcher.py, test_technicals_fetcher.py, etc.) serve dual purpose: validate extraction strategies AND document the multi-strategy pattern for future maintainers.
