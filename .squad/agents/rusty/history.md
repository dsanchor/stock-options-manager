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

### Dashboard Position DTE Sorting (2026-04-02)
**User preference:** Open calls and puts on dashboard should be ordered by DTE (days to expiration) in ascending order.
**Implementation:** Added sort in `_build_dashboard_tables()` after building rows for position monitors. Positions with lower DTE (expiring sooner) appear first.
**Location:** `web/app.py` line 797-799
**Sort key:** `lambda r: (r.get("dte") is None, r.get("dte") or 0)` — handles None values by pushing them to the end.
**Pattern:** Position monitor DTE is already populated from latest activity data. Sort is applied only for position monitor agents (open_call_monitor, open_put_monitor), not watchlist agents.

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

## Learnings

### Telegram-Optimized Summary Agent (2026-04-02)
**Context:** Summary agent output sent directly to Telegram IM
**Location:** `src/tv_summary_instructions.py`

**IM-Friendly Output Patterns:**
- **Emojis for scanning:** 📞📉👀💤📈📊💰⚠️ provide visual hierarchy on mobile
- **Telegram markdown:** Use `*bold*` for symbols and key metrics (not full markdown syntax)
- **Short lines:** Target < 60 chars per line, max 2-3 lines per symbol
- **Strategic spacing:** Blank lines between symbols for readability
- **Section headers:** Emoji + bold for clear visual breaks

**Format structure:**
```
📞 *ACTIVE CALLS*

*SYMBOL* • Strike/Exp • Key Metric
📊 Market context (< 50 chars)
→ Next action + timeframe

[blank line between symbols]
```

**Key guidelines:**
- Use Greek letter shortcuts (Δ for delta) to save space
- Arrows for direction (IV↑, IV↓) instead of words
- Abbreviations: exp, OTM/ITM, CC, CSP
- Empty sections: 💤 No [category] (not verbose text)
- Tone: Like a pro trader texting concise updates

**Why this matters:**
Mobile IM apps have limited screen real estate. Dense paragraphs are hard to scan. Emojis + bold + short lines = instant comprehension while scrolling. User can quickly assess portfolio status without opening full dashboard.

### Symbol Chat Context Selection (2026-04-02)
**Status:** ✅ Completed  
**Files:**
- `web/templates/symbol_chat.html` — Added context selection checkboxes
- `web/app.py` — Updated `_build_symbol_context()` and endpoints to accept preferences

**Implementation:**
Added user-configurable context selection for symbol detail chat. Three checkboxes control what context is loaded:
1. 📊 TradingView Data — Live market data (overview, technicals, forecast, dividends, options chain)
2. 📈 Current Active Positions — Open positions and watchlist status for the symbol
3. 📋 Last 3 Activities — Recent analysis activities (reduced from 5 to 3 per requirements)

**Key design decisions:**
- **localStorage persistence:** User preferences saved to `symbol_chat_context_prefs` key, restored on page load
- **Backward compatibility:** All checkboxes default to checked (preserves existing behavior)
- **Dynamic context building:** Backend conditionally includes sections based on preferences dict
- **Frontend feedback:** Welcome message adapts to show which context types are loaded

**Architecture pattern:**
```javascript
// Frontend: Send preferences in POST body
const prefs = {tradingview: bool, positions: bool, activities: bool};
POST /api/symbols/{symbol}/chat/context { preferences: prefs }

// Backend: Build context conditionally
if preferences.get('tradingview', True):
    # Include TradingView data
if preferences.get('positions', True):
    # Include symbol config with positions
if preferences.get('activities', True):
    # Include last 3 activities (not 5)
```

**Why this matters:**
Different chat scenarios need different context. Quick technical questions don't need full position history. Position management questions don't need all TradingView data. User control reduces token usage and improves response relevance. LocalStorage persistence means users set it once per workflow preference.

### Unified Activities List with Alert Filter (2026-04-02)
**Status:** ✅ Completed  
**Files:**
- `web/app.py` — Merged alerts and activities into single unified list (lines 973-1013)
- `web/templates/symbol_detail.html` — Removed separate alerts card, updated activities card with unified columns (lines 351-426)
- `web/static/app.js` — Added alerts-only toggle filter logic (lines 126-200)

**Implementation:**
Unified the previously separate "Recent Alerts" and "Recent Activities" cards on symbol detail pages into a single chronological list. User can now see the full timeline without mental interleaving. Added 📢 megaphone icon for alerts (same as dashboard pattern). Added "📢 Alerts" filter pill to show only alert items.

**Key design decisions:**
- **Backend merge:** Both `get_recent_activities()` and `get_recent_alerts()` called, combined into single `activities` list sorted by timestamp desc, capped at 80 items (increased from 50)
- **Separate alerts variable preserved:** Still compute `latest_sell_alerts` from alerts-only list for position form pre-fill logic
- **Unified table columns:** Timestamp | Agent | Activity | Strike | Expiration | Underlying | Confidence | Details
  - Alerts show strike, expiration, risk flags in Details column
  - Non-alerts show underlying price, reason in Details column
  - "—" displayed for missing fields
- **Megaphone pattern:** `{% if d.is_alert %}<span class="alert-indicator" title="Alert">📢</span>{% endif %}` in Activity column (matches dashboard.html line 138-140)
- **Filter logic:** Combined time range + alerts-only toggle. JS checks both `data-timestamp` (time cutoff) and `data-is-alert` (alert filter) on each row
- **Badge count:** Dynamically updates to show visible item count after filtering

**Why this matters:**
Users couldn't tell chronological order between alerts and activities when split into separate cards. Monitoring requires temporal context — "did the alert come before or after this activity?" Unified list solves this. Alerts-only filter lets users quickly review signals without scrolling through monitor status updates. Maintains same data availability (alerts list still exists for position form logic) while improving UX.

**Pattern for future work:**
When displaying time-series data with multiple types (alerts, activities, events), prefer single unified chronological view with type filters over separate cards. Users scan top-to-bottom for recency; splitting forces mental timline reconstruction.

### Anti-403 Architecture (2026-07-11)
**Status:** ✅ Implemented (all 4 phases)

**Key Architecture Decisions:**
- **Per-symbol session isolation:** Each symbol gets its own `create_fetcher()` context. This prevents one symbol's 403 from tainting others. The fetcher is now inside the symbol loop, not wrapping it.
- **Monitor agents scope fetcher per-symbol, not per-position:** Multiple positions for the same symbol share one fetcher since they hit the same TradingView pages.
- **403 state is local to `fetch_all()`, not instance-level:** Uses a mutable `_has_403` dict instead of `self.has_403`. The result dict includes `tv_403: bool` so callers check data, not fetcher state.
- **Graduated 403 recovery:** `_handle_403()` does exponential backoff (5s→15s→45s) with session refresh between retries. Only after all retries exhausted does it mark the symbol as blocked.
- **Symbol randomization:** `random.shuffle()` in all 4 agent files, guarded by `config.tradingview_randomize_symbols`, only on multi-symbol runs.
- **Homepage warm-up:** Optional `_warmup()` visits homepage for organic cookies. Defaults to off.

**Key File Paths:**
- `src/tv_data_fetcher.py` — `_handle_403()`, `_warmup()`, `_refresh_session()`, updated `fetch_all()`, `create_fetcher()`
- `src/config.py` — 4 new properties: `tradingview_warmup_enabled`, `tradingview_max_403_retries`, `tradingview_retry_delays`, `tradingview_randomize_symbols`
- `config.yaml` — `tradingview:` section expanded with 4 new keys
- All 4 agent files — per-symbol fetcher + randomization
- `src/agent_runner.py` + `web/app.py` — switched from `fetcher.has_403` to `data.get("tv_403")`


### Anti-403 Implementation (4 Phases) — 2026-04-06T14:10Z
**Status:** ✅ Completed  
**Timestamp:** 2026-04-06T14:10Z  
**Commit:** `831b95e` — feat: implement 4-phase anti-403 architecture for TradingView fetching  
**Scope:** TradingView fetcher resilience across 9 files

**Phase 1: Per-Symbol Session Isolation**
- Moved `async with create_fetcher(config) as fetcher` inside symbol loop (all 4 agent files)
- Each symbol gets fresh HTTP session + Playwright browser lifecycle
- Removed global `has_403` instance flag; replaced with local `_has_403` dict in `fetch_all()`
- `fetch_all()` returns `tv_403: bool` in result dict (stateless, caller-owned)
- Monitor agents scope fetcher per-symbol, NOT per-position (positions in same symbol share fetcher)
- Files: `covered_call_agent.py`, `cash_secured_put_agent.py`, `open_call_monitor_agent.py`, `open_put_monitor_agent.py`

**Phase 2: Graduated 403 Recovery**
- Implemented `_handle_403()` async method with exponential backoff (5s → 15s → 45s, configurable)
- Between retries: close old session, create fresh `requests.Session` with random headers
- After max retries exhausted (default 3), raise HTTPError
- `fetch_all()` catches HTTPError and sets `tv_403=True` for remaining resources
- Separate handling for non-403 transient errors in `_with_retry()` (5s, 10s delays)
- Files: `src/tv_data_fetcher.py`

**Phase 3: Symbol Randomization**
- Added `random.shuffle(symbols_list)` in all 4 agent files
- Only when processing ALL symbols (not single-symbol runs — preserves test determinism)
- Gated by `config.tradingview_randomize_symbols` (default: True)
- Files: `covered_call_agent.py`, `cash_secured_put_agent.py`, `open_call_monitor_agent.py`, `open_put_monitor_agent.py`

**Phase 4: Homepage Warm-Up**
- Implemented `_warmup()` async method visiting https://www.tradingview.com/
- Establishes organic cookies before data fetching
- Called at start of `fetch_all()` when `warmup_enabled=True`
- Defaults to False (conservative); can be enabled in config for higher resilience
- Files: `src/tv_data_fetcher.py`

**Configuration Additions**
- `src/config.py` — 4 new properties: `max_403_retries`, `_403_retry_delays`, `warmup_enabled`, `tradingview_randomize_symbols`
- `config.yaml` — New tradingview section with env var substitution

**API Layer Updates**
- `src/agent_runner.py` — Updated to check `data.get("tv_403")` instead of `fetcher.has_403`
- `web/app.py` — Updated to check `data.get("tv_403")` instead of `fetcher.has_403`

**Architecture Impact**
- Blast radius reduced from global to per-symbol
- Session state now in result dict (stateless design)
- No global state pollution across symbol fetches
- Exponential backoff configurable; defaults tuned for TradingView rate limiting
- Expected 403 rate reduction from 20–30% to <5%

**Testing**
- Basher wrote 28 comprehensive tests in `tests/test_anti403.py`
- Coverage: session isolation (6), 403 recovery (8), global state isolation (4), warmup (3), randomization (4), config loading (3)
- All 28 tests passing ✅
- Edge case discovered (non-blocking): `tv_403` flag in `fetch_all()` unreachable; recommend next-iteration fix

**Related Orchestration**
- `.squad/orchestration-log/2026-04-06T14-10-rusty-anti403.md` (task deliverable)
- `.squad/orchestration-log/2026-04-06T14-10-basher-anti403.md` (test deliverable)
- `.squad/log/2026-04-06T14-10-anti403-implementation.md` (session summary)
- `.squad/decisions/decisions.md` → "Anti-403 Implementation (4 Phases)"

### Position Notes Editing (2026-07-12)
**Status:** ✅ Completed

**Files:**
- `src/cosmos_db.py` — Added `update_position_notes()` method (follows `close_position` pattern: find-by-id, mutate, replace_item)
- `web/app.py` — Added `PATCH /api/symbols/{symbol}/positions/{position_id}/notes` endpoint (follows close/delete error-handling pattern)
- `web/templates/symbol_detail.html` — Inline-editable notes in both table cell and detail panel, with cross-sync between them

**Patterns used:**
- Position lookup: iterate `doc["positions"]` array, match on `position_id`, mutate in-place, `replace_item`
- API error handling: ValueError→404, RuntimeError→503, Exception→500
- UI inline edit: click-to-show textarea + Save/Cancel buttons, `e.stopPropagation()` to avoid row toggle
- Notes exclusion added to expandable row click handler to prevent accidental toggling

## Learnings

### Error Count Metric for TradingView Fetch Stats (2026-07-13)
**Context:** Added error tracking to runtime telemetry UI. Error flag already exists in CosmosDB telemetry docs (written by `agent_runner.py`).

**Changes made:**
- `src/cosmos_db.py` — `get_telemetry_stats()`:
  - Added `"error_count": 0` to `_empty_tv_buckets()` initialization
  - In aggregation loop, read `doc.get("error", False)` and increment `b["error_count"]` when True
  - Included `"error_count"` in final TV stats dict output
- `web/templates/settings_runtime.html`:
  - Added "Errors" column to TradingView Fetch Stats table
  - Display format: `today_errors / 7d_errors / 30d_errors` (e.g., "0 / 2 / 5")
  - Red color (#e74c3c) when any errors present, green (#27ae60) when all zero

**Pattern:** Telemetry metric aggregation follows bucket→accumulate→compute pattern. New metrics require initialization in `_empty_*_buckets()`, accumulation in doc loop, and inclusion in final stats dict.

### Error Count Metric Addition (2026-04-08T12:55:00Z)
**Status:** ✅ Completed  
**Timestamp:** 2026-04-08T12:55:00Z  
**Scope:** Spawn manifest execution (1 task)

**Task:** Add error count metric to TradingView fetch runtime stats

**Files:**
- `src/cosmos_db.py` — Error count aggregation and telemetry tracking
- `web/templates/settings_runtime.html` — Dashboard UI for error metrics

**Summary:**
Extended `_aggregate_runtime_stats()` method to track error counts across daily, 7-day, and 30-day windows. Telemetry buckets now include `error_count` field alongside response_time and success_count. Dashboard "Errors" column displays today/7d/30d counts with conditional color coding (green ≤5, red >5) for at-a-glance operator visibility into fetch reliability.

**Pattern:** Consistent with existing telemetry patterns (response_time, success_count). Error aggregation follows same bucket strategy.

### Row-Level Play Buttons on Dashboard (2026-04-07)
**Status:** ✅ Completed  
**Files:**
- `web/templates/dashboard.html` — Added play button column to agent tables
- `web/static/app.js` — Added row-level trigger button event handler
- `web/static/style.css` — Added `.btn-trigger-row` compact button styling

**Implementation:**
Added individual "▶" play button to each row in all agent tables on the main dashboard. Allows users to trigger single-symbol analysis without navigating away from the dashboard.

**Key design decisions:**
- **Column placement:** New empty-header column at far right of each table
- **Button class:** `.btn-trigger-row` (separate from header `.btn-trigger` to avoid handler conflicts)
- **Compact styling:** Smaller padding (0.15rem 0.4rem), smaller font (0.7rem), just the ▶ icon
- **Same color scheme:** Uses `var(--accent-green)` matching header trigger buttons
- **State transitions:** ▶ → ⏳ (orange, running) → ✓ (blue, done) or ✗ (red, error) → ▶ (reset after 3s)
- **Event propagation:** `e.stopPropagation()` prevents row click navigation when clicking play button
- **API integration:** POST to `/api/trigger/{agent_type}` with body `{"symbol": symbol}` (backend already supported this)
- **Data attributes:** `data-agent="{agent.key}"` and `data-symbol="{row.symbol}"` for routing

**Why this matters:**
Users previously had to either run ALL symbols for an agent (header button) or navigate to symbol detail page to trigger individual analysis. The row-level play button enables quick spot-check analysis directly from the dashboard — useful for re-analyzing specific positions after market events or checking watchlist symbols without full agent runs.

**Pattern for future work:**
When adding inline actions to table rows with clickable row handlers, always use `e.stopPropagation()` to prevent navigation and use distinct CSS classes to avoid event handler conflicts.

---

## Latest — Row Play Button Deployment (2026-04-08T13:11Z)

**Status:** ✅ Deployed  
**Session Log:** `.squad/log/2026-04-08T13-11-play-button.md`  
**Orchestration Log:** `.squad/orchestration-log/2026-04-08T13-11-rusty.md`

**What:** Play button feature (implemented 2026-04-07) successfully deployed to production dashboard.  
**Files:** `web/templates/dashboard.html`, `web/static/app.js`, `web/static/style.css`

**Status:** All agent tables now display ▶ button per row. Button styling: compact green (idle) → blue spinner (running) → green checkmark (done) / red X (error) → reset to ▶.

**Technical:** Posts to `/api/trigger/{agent_type}` with `{"symbol": "..."}` body. Uses `e.stopPropagation()` to prevent row navigation conflicts.

**Ready for:** QA validation, production integration testing.

## Learnings

### Sequential Full Analysis Pattern (2026-07)
The "Run Full Analysis" button now uses `POST /api/trigger-all` which runs all 4 agents sequentially in a single background thread. Progress is stored in `app.state._full_analysis_status` and polled via `GET /api/trigger-all/status`. The status dict auto-resets 30s after completion. The frontend disables all individual trigger buttons during a full run to prevent conflicts. This replaces the old pattern of firing 4 parallel `/api/trigger/{type}` calls from the frontend.

**Files:** `web/app.py` (endpoints + worker), `web/static/app.js` (polling UI)

### Agent Type Filter for Dashboard & Symbol Detail (2026-07)
Added agent type dropdown filters to both the dashboard Recent Activity section and the symbol detail Recent Activities table. The dropdowns are dynamically populated from the rendered items' `data-agent-type` attributes, using the human-friendly agent labels as display text and the agent_type key as the value. Filters compose with the existing time range and symbol filters via `applyDashboardFilters()` and `applyTableFilter()`. The `applyTableFilter` function gained an optional `agentFilterId` parameter (backward-compatible). Pattern: use a `Map` keyed by agent_type to deduplicate options, extract display labels from DOM elements (`.activity-agent` span on dashboard, second `<td>` on symbol detail).

**Files:** `web/templates/dashboard.html`, `web/templates/symbol_detail.html`, `web/static/app.js`

### Agent Filter Bug Fix — _agent_key pattern (2026-07)
**Bug:** Agent name filter did nothing on dashboard or symbol detail pages. The `data-agent-type` HTML attributes were rendering from `{{ item.agent_type }}` which could produce empty strings with Cosmos dict-like objects.

**Fix:** Introduced explicit `_agent_key = str(d.get("agent_type", ""))` in both route handlers (`app.py` dashboard ~line 932, symbol_detail ~line 991). Templates now use `{{ item._agent_key | default('', true) }}` instead of `{{ item.agent_type }}`. JS comparisons add `.trim()` for robustness.

**Pattern:** When passing Cosmos document fields to Jinja2 `data-` attributes, always extract to an explicit string field in the route handler rather than relying on Jinja2 attribute access on dict-like objects.

**Files:** `web/app.py`, `web/templates/dashboard.html`, `web/templates/symbol_detail.html`, `web/static/app.js`

### Symbol Position Report Feature (2026-07)
Added `POST /api/symbols/{symbol}/report` endpoint that generates a comprehensive LLM-powered position report in Spanish. The endpoint gathers all available data (CosmosDB symbol doc, last 3 activities per agent type, cached TradingView data) into a single context string, then calls Azure OpenAI with `max_completion_tokens=4096` and a structured system prompt specifying 7 report sections. Frontend uses a modal overlay with a simple markdown-to-HTML converter (handles headers, bold, tables, code). Uses cache only (no `force_refresh`) to avoid slow fetches during report generation. The report button sits next to the existing Chat link in the symbol detail header.

**Files:** `web/app.py` (endpoint ~line 1086), `web/templates/symbol_detail.html` (button + modal + JS)
