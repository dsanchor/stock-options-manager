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

### Dict-Spread Protection Pattern (EXPANDED)
When using `**spread` in Python dict literals, reassert ALL routing/identity fields after the spread — not just `id` and `timestamp`. Any field used in CosmosDB queries (e.g., `doc_type`, `symbol`, `agent_type`) MUST be protected, because LLM-generated dicts can contain arbitrary keys that silently overwrite them. The `doc_type` field is especially critical since it's used in every `WHERE` clause for document classification.

### Symbol Detail Page Layout
Alerts card appears BEFORE activities card in `web/templates/symbol_detail.html`. User preference: alerts are higher priority and should be seen first.

### Lazy Initialization of Expensive Resources
Playwright + Chromium are expensive. Initialize lazily via helper method (`_ensure_browser()`) rather than in `__init__`. Saves resources when only lightweight fetchers (BS4) run.

### Multi-Strategy Data Extraction
Implement 3-level fallback: (1) targeted HTML extraction, (2) embedded JSON parsing, (3) API fallback. Each strategy provides value-add error handling and graceful degradation.

### TradingView Scanner API for Validation
The unauthenticated `/america/scan` endpoint (not `/options/scan2`) provides fundamentals, technicals, forecast, and dividends data without browser context. Returns "Unknown field" for invalid columns.

### Chat Markdown Rendering
Both `web/templates/chat.html` (general) and `web/templates/symbol_chat.html` (per-symbol) share the same `addMessage()` pattern. Markdown rendering via `marked.js` (CDN in `base.html`) applies only to assistant messages; user messages stay as `textContent`. The `.markdown-body` class on assistant bubbles overrides `white-space: pre-wrap` to `normal` so rendered HTML flows correctly. CSS for rendered markdown lives in `web/static/style.css` under the "Rendered Markdown inside chat bubbles" section.

### Test Scripts as Documentation
Standalone test scripts (test_fetcher.py, test_technicals_fetcher.py, etc.) serve dual purpose: validate extraction strategies AND document the multi-strategy pattern for future maintainers.

### Settings Data Source Pattern (2026-07)
**Rule:** Any web route that displays user-configurable settings (scheduler cron, timezone, telegram config) MUST read from CosmosDB first, falling back to `config.yaml` only if CosmosDB is unavailable. Pattern: `cosmos_settings = _load_settings_from_cosmos(cosmos); config = cosmos_settings if cosmos_settings else _load_config()`. The `_load_config()` function reads the baked-in `config.yaml` which resets to defaults on every deploy. Only use `_load_config()` directly for connection credentials (`azure`, `cosmosdb` sections) that are env-var-driven.
- **Key files:** `web/app.py` (dashboard route, telegram test route, settings routes)
- **Related bug fix:** Commit 90c05cd — dashboard was showing config.yaml defaults instead of CosmosDB user settings after deploy.

### Position Enrichment from Activities Pattern (2026-07)
When displaying open positions, enrich them with data from the latest monitor activity (assignment_risk, moneyness). Pattern: scan the already-fetched activities list for monitor agents (`open_call_monitor`/`open_put_monitor`), build a `position_id → latest activity` lookup, and attach computed fields with `_` prefix (e.g., `_assignment_risk`, `_moneyness`) to avoid polluting the persisted document.
- **Key files:** `web/app.py` (symbol_detail_page route, lines ~900-911), `web/templates/symbol_detail.html` (positions table)
- **CSS classes:** `badge-risk-*` (low/medium/high/critical), `badge-moneyness-*` (otm/atm/itm/deep-itm/deep-otm) — defined in `web/static/style.css`
- **Commit:** e8d56c8

### Run Analysis Button on Symbol Detail (2026-07)
The positions card on the symbol detail page has a "▶ Run Analysis" green `btn-trigger` button that triggers `open_call_monitor` and/or `open_put_monitor` agents depending on active position types. The button only renders when active positions exist (Jinja `selectattr` filter). It reuses the existing `/api/trigger/{agent_type}` endpoint and follows the same sequential promise-chain pattern as the dashboard's "Run Full Analysis" button (see `web/static/app.js`). Active position types (calls/puts) are embedded as `data-has-calls` / `data-has-puts` attributes on the button element.
- **Key files:** `web/templates/symbol_detail.html` (positions card header + script block)
- **Pattern reused from:** `web/static/app.js` (btn-trigger click handler, sequential agent triggering)
- **CSS:** `btn-trigger` green style (defined in `web/static/style.css`)
- **Commit:** 1f686f1

### Alert Pre-fill Checkbox in Add Position Form (2026-07)
**Status:** ✅ Completed → Reworked (see below)
**Commit:** c9ce586
**Files:** `web/app.py` (symbol_detail_page route), `web/templates/symbol_detail.html` (form + script block)
- Original: pre-filled strike/expiration/notes from alert data — user didn't want that behavior

### Alert Attach (Transparent Source Metadata) Fix (2026-07)
**Status:** ✅ Completed
**Files:** `web/app.py` (api_add_position route), `web/templates/symbol_detail.html` (JS block)
- Replaced form pre-fill with transparent source-attach: checkbox now sends `source_activity_id` in POST body
- Backend looks up activity via `cosmos.get_activity_by_id()`, builds `source` dict (same pattern as from-activity route), passes to `cosmos.add_position()`
- No form field manipulation — user fills in strike/expiration/notes manually; alert data rides along as metadata
- No watchlist disable or cascade-delete (that's the from-activity route's behavior only)
- Checkbox visibility: watchlist enabled + alert has `activity_id` (not strike/expiration)
- Label: "Attach latest alert data (agent_label, date)" instead of "Pre-fill from..."

## Learnings

### Source Attach vs Pre-fill Pattern (2026-07)
Two distinct UX patterns for alert→position flow:
1. **From-activity route** (`/positions/from-activity/{id}`): Full automation — creates position, disables watchlist, cascade-deletes activities/alerts. Used when user clicks "create position" on an alert card.
2. **Manual add with attach** (`/positions` + `source_activity_id`): User fills all fields manually; alert source metadata transparently attached. No side effects on watchlist or alerts. Checkbox is just a toggle.
The `source` dict construction is identical in both paths — factored from the activity document via `cosmos.get_activity_by_id()`.

## Spawn Manifest — 2026-03-31

### rusty-alert-attach-fix
**Status:** Merged to history  
**Commit:** 2026-03-31T15:34:10Z (scribe consolidation)

**Summary:** Fixed alert checkbox in symbol detail to transparently attach alert source data on submit instead of pre-filling form fields.

**Decisions Merged:**
1. Alert Pre-fill Pattern for Position Forms (2026-07) — JavaScript embedding of latest_sell_alerts
2. Alert Checkbox Attaches Source Metadata (2026-07) — Backend lookup via source_activity_id
3. Protect all routing fields from dict-spread override (2025-07-24) — Reassert doc_type/identity fields

**Orchestration Log:** `.squad/orchestration-log/2026-03-31T15:34:10Z-rusty.md`

**Impact Scope:** Web UI only; no downstream agent changes.


---

## Alert Checkbox Behavior (2026-03-31)
**Status:** ✅ Complete

Alert pre-fill for Add Position form now transparently attaches source activity metadata instead of pre-filling form fields. Checkbox sends `source_activity_id` to backend; backend builds `source` dict. No form field side effects.

**Files Changed:**
- web/app.py
- web/templates/symbol_detail.html

**Notes for Downstream:**
- Position document includes `source` metadata when checkbox used
- No watchlist cascade changes
- No new CosmosDB queries

---

## Earnings Gate Schema Change (2026-07-09)
**Status:** ✅ Noted

Linus implemented mandatory earnings gate across all 4 instruction files. All agent responses now include `earnings_analysis` JSON object as first analytical step.

**Impact:**
- New field: `earnings_analysis` (non-breaking addition)
- JSON extraction in agent_runner should handle transparently
- Consider updating any downstream schema validation if present

**Files Changed (by Linus):**
- src/tv_covered_call_instructions.py
- src/tv_cash_secured_put_instructions.py
- src/tv_open_call_instructions.py
- src/tv_open_put_instructions.py
