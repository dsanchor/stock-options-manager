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

## Recent Tasks (2026-04)

### Quick Analysis Summary Table + Activity Navigation (2026-04-02T22:13:22Z)
**Status:** ✅ Completed  
**Timestamp:** 2026-04-02T22:13:22Z  
**Scope:** Spawn manifest execution (2 tasks)

#### Task 1: Enhanced Quick Analysis Chat with Decision Summary Table
**Files:**
- `src/tv_open_call_chat_instructions.py` — Chat call analysis with decision table
- `src/tv_open_put_chat_instructions.py` — Chat put analysis with decision table

**Summary:**
Added mandatory Decision Summary Table to quick analysis chat instructions. Table includes 9–10 key decision factors: overall recommendation, reasons against/for, suggested strikes and dates, earnings gate status, technical gate status, primary risk, profit target/exit plan, and (for puts) assignment readiness. Conversational analysis (3–5 paragraphs) → Structured decision table. Uses actual numbers (prices, deltas, DTE, earnings timing) and balances risk/opportunity presentation.

**Decision Record:** `.squad/decisions/decisions.md` → "Quick Analysis Chat Decision Summary Table Pattern"

#### Task 2: Fixed Activity Navigation
**Files:**
- `web/templates/symbol_detail.html` — Activity row navigation

**Summary:**
Updated clickable row navigation in activity table to link to activity details instead of symbol pages. Improves user workflow and information architecture for activity drilling.

### Alert Visibility Fix + Display Reorder (2026-03-31)
**Status:** ✅ Completed  
**Files:**
- cosmos_db.py: Protected `doc_type` from `**data` spread override in write_activity() and write_alert()
- symbol_detail.html: Moved alerts section before activities section

### Quick Analysis Chat Conversationalization (2026-04-01T10:51:20Z)
**Status:** ✅ Completed  
**Duration:** ~265s  
**Files:**
- `src/tv_open_call_chat_instructions.py` (NEW) — Conversational call analysis
- `src/tv_open_put_chat_instructions.py` (NEW) — Conversational put analysis
- `web/app.py` — Updated chat endpoints to use `*_chat_instructions.py`

**Summary:**
Converted Quick Analysis chat from JSON/structured output to natural language responses. Created separate instruction sets for chat UI (conversational) and background monitor agents (structured JSON). Both share same TradingView data source; output format optimized for audience type.

### TradingView Symbol Info Widget (2026-04-01T12:38:07Z)
**Status:** ✅ Completed  
**Duration:** ~118s  
**Files:**
- `web/templates/symbol_detail.html` — Replaced static label with TradingView widget

**Summary:**
Integrated live TradingView symbol info widget into symbol detail page. Replaced static "Market:Symbol" text label with interactive widget displaying real-time trading data.

## Key Learnings & Patterns

### Unified Schema Query Pattern (2026-04-01)
Activities and alerts live in the same container with `doc_type='activity'`. Discriminate with `is_alert` boolean:
- **Alerts:** `WHERE c.doc_type = 'activity' AND c.is_alert = true`
- **Activities (excluding alerts):** `WHERE c.doc_type = 'activity' AND (c.is_alert = false OR NOT IS_DEFINED(c.is_alert))`

ID format: `{symbol}_{agent_type}[_{position_id}]_{ts_compact}` — no prefixes.

### Dict-Spread Protection Pattern
When using `**spread` in Python dict literals, reassert ALL routing/identity fields after spread (id, timestamp, doc_type, symbol, agent_type). LLM-generated dicts can contain arbitrary keys that silently overwrite critical fields. The `doc_type` field especially critical since it's used in every WHERE clause for document classification.

### Symbol Detail Page Layout
Alerts card appears BEFORE activities card in `web/templates/symbol_detail.html`. User preference: alerts are higher priority and should be seen first.

### Lazy Initialization of Expensive Resources
Playwright + Chromium are expensive. Initialize lazily via helper method (`_ensure_browser()`) rather than in `__init__`. Saves resources when only lightweight fetchers (BS4) run.

### Multi-Strategy Data Extraction
Implement 3-level fallback: (1) targeted HTML extraction, (2) embedded JSON parsing, (3) API fallback. Each strategy provides value-add error handling and graceful degradation.

### TradingView Scanner API for Validation
The unauthenticated `/america/scan` endpoint provides fundamentals, technicals, forecast, and dividends data without browser context. Returns "Unknown field" for invalid columns.

### Position Enrichment from Activities
When displaying open positions, enrich with data from latest monitor activity (assignment_risk, moneyness). Pattern: scan activities for monitor agents, build `position_id → latest activity` lookup, attach computed fields with `_` prefix (e.g., `_assignment_risk`, `_moneyness`) to avoid polluting persisted document.

### Settings Data Source Pattern (2026-07)
Any web route displaying user-configurable settings MUST read from CosmosDB first, falling back to `config.yaml` only if unavailable. Pattern: `cosmos_settings = _load_settings_from_cosmos(cosmos); config = cosmos_settings if cosmos_settings else _load_config()`. Only use `_load_config()` directly for connection credentials.

### Source Attach vs Pre-fill Pattern (2026-07)
Two distinct UX patterns for alert→position:
1. **From-activity route:** Full automation — creates position, disables watchlist, cascade-deletes activities/alerts
2. **Manual add with attach:** User fills fields manually; alert source metadata transparently attached. No side effects.

### Run Analysis Button on Symbol Detail
The positions card on symbol detail has "▶ Run Analysis" button that triggers open_call_monitor and/or open_put_monitor agents depending on active position types. Button only renders when active positions exist. Reuses `/api/trigger/{agent_type}` endpoint.

### Earnings Gate Schema (2026-07-09)
Mandatory earnings gate across all 4 instruction files. All agent responses now include `earnings_analysis` JSON object as first analytical step. Non-breaking addition (new field, existing fields unchanged).

### Summary Agent Categorization (2026-07-09)
Updated summary agent to organize daily reports into four sections: Current Calls, Current Puts, Watchlist Calls, Watchlist Puts. Empty sections show "No X" messages.

### Alert Link Bug Fix (2026-04-02)
**Issue:** Symbol detail page alert links generated 404s while activity links worked. Dashboard links worked for both.
**Root cause:** Alert row template used non-existent field `alt.activity_id` instead of `alt.id`.
**Fix:** Changed alert template from `data-href="/activities/{{ alt.activity_id }}"` to `data-href="/activities/{{ alt.id }}"` to match activities and dashboard patterns.
**Pattern:** Both activities and alerts are documents with an `id` field. Always use `{item}.id` for activity detail links, never invent intermediate field names.

---

## Scribe Orchestration Records (2026-04)

### 2026-04-02T22:35:22Z — Alert Link Fix Summary

**Status:** ✅ Documented  
**Summary:** Completed alert link navigation fix. Updated decisions.md with pattern documentation for consistent ID field usage across activities and alerts.



## Archived Work (March 2026)

Earlier phases and implementation details archived to `.squad/decisions/decisions.md` and commit history. See that file for:
- Phase 1–4a CosmosDB Refactor (architecture, implementation, commits)
- Chat UI design system alignment
- Button enable/disable state fixes
- Position management UI enhancements
- Earnings gate architecture decisions
- JSON format hints and instruction improvements
- Alert checkbox behavior and source attachment
