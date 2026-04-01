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

### Quick Analysis Chat Conversationalization (2026-04-01T10:51:20Z)
**Status:** ✅ Completed  
**Duration:** ~265s  
**Files:**
- `src/tv_open_call_chat_instructions.py` (NEW) — Conversational call analysis
- `src/tv_open_put_chat_instructions.py` (NEW) — Conversational put analysis
- `web/app.py` — Updated chat endpoints to use `*_chat_instructions.py`
- `.squad/decisions.md` — Added decision record: "Chat vs Monitor Instructions Split"

**Summary:**
Converted Quick Analysis chat from JSON/structured output to natural language responses. Created separate instruction sets for chat UI (conversational) and background monitor agents (structured JSON). Both share same TradingView data source; output format optimized for audience type.

**Key Design:**
- Monitor agents: `TV_OPEN_{CALL|PUT}_INSTRUCTIONS` → JSON for database
- Chat interface: `TV_OPEN_{CALL|PUT}_CHAT_INSTRUCTIONS` → Prose for humans
- Core analysis logic shared; output presentation differs by use case

### TradingView Symbol Info Widget (2026-04-01T12:38:07Z)
**Status:** ✅ Completed  
**Duration:** ~118s  
**Requested by:** dsanchor  
**Files:**
- `web/templates/symbol_detail.html` — Replaced static label with TradingView widget

**Summary:**
Integrated live TradingView symbol info widget into symbol detail page. Replaced static "Market:Symbol" text label with interactive widget displaying real-time trading data.

**Features:**
- Dark theme styling for UI consistency
- Transparent background integration
- Responsive width to container
- No additional dependencies required
- Live price and technical data display

## Learnings

### Unified Schema Query Pattern (2026-04-01)
Activities and alerts live in the same container with the same `doc_type='activity'`. Use `is_alert` boolean discriminator:
- **Alerts:** `WHERE c.doc_type = 'activity' AND c.is_alert = true`
- **Activities (excluding alerts):** `WHERE c.doc_type = 'activity' AND (c.is_alert = false OR NOT IS_DEFINED(c.is_alert))`
- **All activities (including alerts):** `WHERE c.doc_type = 'activity'`

The `NOT IS_DEFINED(c.is_alert)` clause handles legacy documents that don't have the field. After migration, all documents will have `is_alert` explicitly set.

**ID Format:** `{symbol}_{agent_type}[_{position_id}]_{ts_compact}` — no prefixes. Old format had `dec_` for activities and `sig_` for alerts.

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

## Summary Agent Categorization Update (2026-07-09)
**Status:** ✅ Complete

Updated the summary agent to organize daily reports into four distinct sections based on position status and option type.

**Sections:**
1. **Current Calls** - symbols with active call positions (open_call_monitor)
2. **Current Puts** - symbols with active put positions (open_put_monitor)
3. **Watchlist Calls** - symbols watched for covered call opportunities (covered_call, no positions)
4. **Watchlist Puts** - symbols watched for cash-secured put opportunities (cash_secured_put, no positions)

**Key Changes:**
- Modified `TV_SUMMARY_INSTRUCTIONS` in `src/tv_summary_instructions.py`
- Added section headers with `=== SECTION NAME ===` format
- Empty sections now show simple "No X" messages instead of being omitted
- Agent categorizes based on `agent_type` field in activities

**Files Changed:**
- src/tv_summary_instructions.py

**Pattern Notes:**
- The summary agent receives activities grouped by symbol from `cosmos.get_recent_activities_by_symbol()`
- Each activity has an `agent_type` field that indicates its purpose (covered_call, cash_secured_put, open_call_monitor, open_put_monitor)
- Monitor agents (open_call_monitor, open_put_monitor) track active positions
- Sell agents (covered_call, cash_secured_put) watch for new sell opportunities

## Chat UI Design System Alignment (2024-03-31)
**Status:** ✅ Complete
**Commit:** 1baaaab

Refactored the dual-mode chat interface to align with the application's existing design system instead of using custom styling.

**Key Changes:**
1. **Mode Selection Cards** — Replaced custom `.mode-option` CSS with inline styles using standard design tokens (`var(--bg-input)`, `var(--bg-hover)`, `var(--border)`, `var(--accent-blue)`)
2. **Quick Analysis Form** — Changed market input from dropdown (`<select>`) to free text field (`<input type="text">`) for flexibility
3. **Unified Navigation** — Standardized back buttons across all screens using existing `.btn-sm` class instead of custom button styles
4. **Form Consistency** — Used standard `.card-header`, `.hint`, and `.input-field` classes matching `settings_config.html` patterns
5. **CSS Cleanup** — Removed 30+ lines of unused `.mode-option`, `.mode-icon` custom styles and responsive overrides

**Design Patterns Applied:**
- Card structure: `.card` → `.card-header` → content padding
- Form labels: `font-size: 0.85rem; color: var(--text-muted); margin-bottom: 0.3rem`
- Input styling: `.input-field` with `var(--bg-input)`, `var(--border)`, consistent padding
- Navigation buttons: `.btn-sm` for secondary actions in card headers

**Files Changed:**
- `web/templates/chat.html` — HTML structure and JavaScript variable names
- `web/static/style.css` — Removed custom chat mode CSS

**User Experience:**
- Both Portfolio Chat and Quick Analysis now have identical navigation patterns
- Market field accepts any text (e.g., "NASDAQ", "NYSE", or custom exchanges)
- Visual consistency with dashboard, settings, and symbol detail pages

## Quick Analysis Button Enable Fix (2024-03-31)
**Status:** ✅ Complete

Fixed the "Fetch & Analyze" button in Quick Analysis mode to properly enable/disable based on input field state across all navigation scenarios.

**Problem:**
- Button started enabled but had no event listeners to update state as inputs changed
- Navigation scenarios (mode switch, back button, pre-filled values) didn't properly evaluate button state
- Enter key behavior was inconsistent (symbol Enter would try to fetch even with empty market)

**Solution:**
1. Button now starts `disabled` by default
2. Added `checkFetchButtonState()` function that enables button only when both symbol and market have values
3. Attached `input` event listeners to both fields for real-time validation
4. Call state check when Quick Analysis mode is first selected
5. Improved Enter key UX: symbol→focus market, market→fetch (only if button enabled)

**Navigation Scenarios Covered:**
- Fresh page load → select Quick Analysis → button disabled until both fields filled ✅
- Portfolio Chat → switch to Quick Analysis → button state evaluated on mode entry ✅
- Type in fields → button enables/disables in real-time as values change ✅
- Clear a field → button immediately disables ✅
- Browser back/forward → state re-evaluated on mode display ✅

**Files Changed:**
- `web/templates/chat.html` (lines 53, 109-127, 300-319)

**Pattern Notes:**
- Standard form validation pattern: disable by default + enable on valid state
- Use `input` events (not `keyup`) for cross-browser paste/autofill support
- Always check state on mode entry to handle pre-filled or persisted form values
- Enter key navigation should respect button disabled state


## Session Completion (2026-04-01T10:22:15Z)
**Status:** ✅ Complete  
**Decision:** Quick Analysis Button Enable Pattern merged to decisions.md  
- Established reusable pattern for form validation with disabled buttons
- Documented as decision #10 for team reference
- Scribe logged orchestration and session records

## Quick Analysis Put/Call Dropdown (2026-04-01)
**Status:** ✅ Complete

Added option type dropdown (Call/Put) to Quick Analysis chat that triggers automatic first analysis using the same centralized instructions as monitoring agents.

**Key Architecture:**
1. **Centralized Instructions Reuse** — Quick Analysis first message now imports and uses `TV_OPEN_CALL_INSTRUCTIONS` or `TV_OPEN_PUT_INSTRUCTIONS` from the same files that `open_call_monitor` and `open_put_monitor` agents use. Single source of truth.

2. **Automatic First Analysis** — When user fetches a symbol, the frontend automatically sends a `first_analysis: true` request to the chat endpoint. Backend loads the appropriate instruction template based on `option_type` and sends it to the LLM with the fetched TradingView data. The response is displayed as the first message, then normal chat continues.

3. **Three-Input Form** — Quick Analysis form now requires: Symbol + Market + Option Type (dropdown). Button only enables when all three are filled.

**Files Changed:**
- `web/templates/chat.html` — Added dropdown, automatic first analysis trigger, `awaitingFirstAnalysis` state flag
- `web/app.py` — Updated `/api/chat/fetch-symbol` to accept `option_type`, updated `/api/chat` to handle `first_analysis` flag and import centralized instructions

**Data Flow:**
1. User selects symbol, market, and option type (call/put)
2. Frontend fetches TradingView data with option_type included
3. Frontend automatically sends first analysis request with `first_analysis: true`
4. Backend loads `TV_OPEN_CALL_INSTRUCTIONS` or `TV_OPEN_PUT_INSTRUCTIONS` based on option_type
5. Backend builds system prompt: `{instructions}\n\n{tradingview_data}`
6. LLM analyzes using monitor agent instructions
7. Response displayed as first message
8. User can continue asking follow-up questions (normal chat mode)

**Pattern Notes:**
- Instructions are imported at runtime (not duplicated) to maintain single source of truth
- The `first_analysis` flag changes only the system prompt — all other chat behavior is identical
- Frontend handles the automatic analysis trigger transparently (user just sees "Analyzing...")
- After first analysis, chat behaves like a standard Q&A agent with the data in context

**Key Files:**
- `src/tv_open_call_instructions.py` — Centralized call analysis instructions
- `src/tv_open_put_instructions.py` — Centralized put analysis instructions
- Used by: `open_call_monitor_agent.py`, `open_put_monitor_agent.py`, and now Quick Analysis chat


## Quick Analysis Chat — Conversational Output (2026-04-01)
**Status:** ✅ Complete

Improved Quick Analysis chat to provide human-friendly conversational analysis instead of JSON/structured output.

**Problem:** 
- Quick Analysis chat was using monitor agent instructions (`TV_OPEN_CALL_INSTRUCTIONS` / `TV_OPEN_PUT_INSTRUCTIONS`)
- These instructions were designed for monitor agents and requested JSON output with specific fields
- Chat UI was displaying JSON blocks or structured key-value pairs, not conversational analysis
- User wanted natural, human-readable conversation like talking to an analyst

**Solution:**
1. Created new chat-specific instruction files:
   - `src/tv_open_call_chat_instructions.py` — Conversational call options analysis
   - `src/tv_open_put_chat_instructions.py` — Conversational put options analysis

2. New instructions guide the LLM to:
   - Write like talking to a colleague over coffee
   - Use plain English, no jargon dumps
   - Provide 3-5 natural paragraphs covering: current setup, technicals, earnings timing, opportunity, final thought
   - Avoid JSON, structured output, field-value pairs, or data list dumps
   - Focus on 2-3 key insights that matter, not exhaustive indicator recitation

3. Updated `web/app.py` chat endpoint:
   - First analysis uses `TV_OPEN_CALL_CHAT_INSTRUCTIONS` / `TV_OPEN_PUT_CHAT_INSTRUCTIONS` (conversational)
   - Follow-up questions use conversational system prompt (not monitoring instructions)
   - Both modes now explicitly request natural, friendly responses

**Key Architecture Decision:**
- **Monitor agents** continue using `TV_OPEN_CALL_INSTRUCTIONS` / `TV_OPEN_PUT_INSTRUCTIONS` (JSON output for DB storage)
- **Chat interface** uses new `*_CHAT_INSTRUCTIONS` files (conversational output for users)
- Separate instructions for separate use cases, both pulling from same TradingView data

**Files Changed:**
- `src/tv_open_call_chat_instructions.py` — NEW conversational call analysis instructions
- `src/tv_open_put_chat_instructions.py` — NEW conversational put analysis instructions
- `web/app.py` — Updated imports and system prompts for conversational output

**Pattern:**
- Chat instructions emphasize: "You're a knowledgeable analyst having a conversation, not a data export tool"
- Example response style provided in instructions to guide LLM tone
- Explicit anti-patterns: "DON'T list out every indicator value", "DON'T use structured JSON"
- Markdown rendering in frontend (via `marked.js`) preserves formatting while keeping natural flow

### TradingView Anti-Bot Integration in Web UI (2026-04-01)
- Updated web/app.py to integrate new TradingViewFetcher anti-bot implementation from Linus
- **Change:** Adopted new `create_fetcher(config)` API for fetcher initialization
- **Impact:** Market data fetches now protected by comprehensive anti-bot measures (UA rotation, rate limiting, session management)
- **UI consideration:** Fetch times increased by 5-15s per symbol due to rate limiting; consider adding loading indicators
- **No backend changes needed:** Web API endpoints unchanged; anti-bot measures transparent to frontend
- **Team coordination:** Linus (anti-bot) → Rusty (web integration) completed seamlessly

## Learnings

### TradingView Widget Integration (2026-04-01)
**Context:** User requested replacing the static "Market:Symbol" text label with an embedded TradingView widget

**Changes:**
- Modified `web/templates/symbol_detail.html` (lines 5-28)
- Replaced `<h1>{{ symbol_doc.display_name }}</h1>` with TradingView symbol-info widget embed
- Widget configuration:
  - Dynamic symbol: `{{ symbol_doc.exchange }}:{{ symbol_doc.symbol }}`
  - Dark theme (`colorTheme: "dark"`) to match app aesthetic
  - Transparent background (`isTransparent: true`)
  - Responsive width (`width: "100%"`)
  - Links to TradingView symbol page with "by TradingView" attribution

**Key Pattern:**
- Symbol detail page context provides: `symbol_doc.symbol`, `symbol_doc.exchange`, `symbol_doc.display_name`
- TradingView widgets use `EXCHANGE:SYMBOL` format (e.g., "NASDAQ:AAPL")
- Widget script loaded async from `https://s3.tradingview.com/external-embedding/embed-widget-symbol-info.js`
- JSON config passed inline within script tag

**User Experience:**
- Rich interactive widget replaces static text header
- Shows real-time performance data, price, and key stats
- Maintains consistency with dark theme across the app
- Widget responsive and adapts to screen width

**File Locations:**
- Template: `web/templates/symbol_detail.html`
- Route handler: `web/app.py` (line 934: `@app.get("/symbols/{symbol}")`)
- Data model: `src/cosmos_db.py` (symbol_config document structure)

### Position Management Fixes (2026-04-02)
**Status:** ✅ Completed  
**Files:**
- `web/templates/symbol_detail.html` — Added alert attachment checkbox to manual roll form
- `web/app.py` — Updated manual roll endpoint to accept and process source_activity_id
- `src/cosmos_db.py` — Fixed position_id collision bug, improved close_position robustness

**Changes:**
1. **Alert Attachment Feature:** Added checkbox to roll form that allows attaching latest alert when rolling manually. Checkbox is disabled/greyed if no alerts available. Uses same "Created manually (last alert information attached)" note text as opening positions with alerts.

2. **Position ID Collision Fix:** Added validation in roll_position() to prevent rolling to a strike/expiration that already has an existing position (active or closed). Prevents duplicate position_id issues.

3. **Close Position Robustness:** Made close_position() handle already-closed positions gracefully and close all positions with duplicate IDs (defensive against bad data from before collision fix).

4. **Delete Button:** Verified delete button is already always enabled for all positions (no fix needed).

**Key Patterns:**
- Alert attachment uses same pattern as add_position: embed LATEST_SELL_ALERTS in template, JavaScript checks for alerts, sends source_activity_id in payload
- Backend builds full source object from activity (includes agent_type, activity, confidence, reason, underlying_price, premium, iv, risk_flags, timestamp)
- Position ID format: `pos_{SYMBOL}_{TYPE}_{STRIKE}_{EXPIRATION_COMPACT}` - collision check prevents duplicate IDs
- Roll form tracks position type via `data-pos-type` attribute for proper alert lookup

**User Experience:**
- Users can now attach alert data when manually rolling positions, providing same traceability as opening from alerts
- Clear feedback when rolling would create a collision ("a position with these parameters already exists")
- More robust close operation handles edge cases gracefully

**File Locations:**
- Template: `web/templates/symbol_detail.html` (lines 125-149: roll form with checkbox)
- API endpoint: `web/app.py` (lines 544-610: api_manual_roll_position with alert support)
- Backend: `src/cosmos_db.py` (lines 224-282: roll_position with collision check; lines 284-304: close_position robustness)

### Completion Log (2026-04-02)
**Status:** ✅ Session Archived  
**Orchestration:** 2026-04-01T21:17:17Z-rusty.md  
**Session Log:** 2026-04-01T21:17:17Z-position-management-fixes.md  

All position management fixes delivered and documented. No open items.

### Unified Schema Implementation (2026-04-01)
**Status:** ✅ Completed  
**Requested by:** dsanchor  
**Context:** Danny's CosmosDB migration design (.squad/decisions/inbox/danny-cosmosdb-migration.md)  
**Files:**
- `src/cosmos_db.py` — Updated write/query methods for unified schema
- `web/app.py` — No changes needed (uses cosmos_db.py methods)

**Summary:**
Implemented unified schema changes based on Danny's migration design. Alerts are now activities with `is_alert=true` rather than separate documents. New ID format drops legacy `dec_` and `sig_` prefixes.

**Key Changes:**
1. **ID Format:** `{symbol}_{agent_type}[_{position_id}]_{ts_compact}` (removed `dec_` prefix)
2. **Alert Model:** Added `mark_as_alert()` method to set `is_alert=true` on existing activity
3. **Query Updates:** All alert queries now filter by `c.is_alert = true` instead of `doc_type='alert'`
4. **Activity Queries:** Now exclude alerts with `(c.is_alert = false OR NOT IS_DEFINED(c.is_alert))`
5. **Backwards Compatibility:** Kept deprecated `write_alert()` with TODO comment for post-migration cleanup

**Implementation Details:**
- `write_activity()`: Changed ID from `dec_{symbol}...` to `{symbol}...`
- `mark_as_alert()`: New method replaces separate alert document creation
- `get_recent_alerts()`: Query updated to `WHERE c.doc_type='activity' AND c.is_alert=true`
- `get_all_alerts()`: Same discriminator update
- `count_alerts_by_symbol()`: Same discriminator update
- `get_recent_activities()`: Now filters out alerts
- `get_all_activities()`: Now filters out alerts
- `get_recent_activities_by_symbol()`: Now filters out alerts
- Cascade delete methods: Added TODO comments for cleanup after migration

**Migration Notes:**
- Old `write_alert()` method kept temporarily with deprecation notice
- Cascade delete logic for old alert documents kept with TODO comments
- All new writes use prefix-free IDs
- All queries use `is_alert` discriminator for unified schema

## Orchestration Session (2026-04-01T21:39:57Z)

**Session:** CosmosDB Unified Schema — Decision Consolidation and Team Orchestration

**Status:** Implementation complete and documented. Awaiting migration script execution and code review.

**Related Agents:**
- Danny: Migration design and strategy finalized
- Linus: agent_runner.py refactored for single-write alert path
- Basher: Migration script ready for dry-run and execution

**Cross-Team Updates:**
- Danny's migration script will transform existing CosmosDB data (offline batch, 2-5 min window)
- Linus's refactored agent_runner will work seamlessly with new unified model
- Basher's script includes dry-run, backup, restore, and progressive validation

**Post-Migration Cleanup (Checklist):**
After migration completes:
1. Remove deprecated `write_alert()` method
2. Remove cascade delete logic for old `doc_type='alert'` documents
3. Remove TODO comments referencing backwards compatibility
4. Update any remaining queries that reference legacy ID prefixes

**Session Log:** `.squad/log/2026-04-01T21-39-cosmosdb-unified-schema.md`  
**Orchestration Log:** `.squad/orchestration-log/2026-04-01T21-39-rusty.md`

