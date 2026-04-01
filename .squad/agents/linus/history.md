# Linus — History

## Project Context
- **Project:** options-agent
- **User:** dsanchor
- **Stack:** Python, Microsoft Agent Framework, Azure Foundry (gpt-5.4-mini)
- **MCP:** iflow-mcp_ferdousbhai_investor-agent 1.6.3
- **Description:** Two periodic trading agents for covered call and cash-secured put sell signals. Local runtime, configurable polling, stock symbols from files, decision logs, sell signal alerts.

## Learnings

### Telegram Settings UI (2026-03-29)
- Added Telegram configuration endpoints to `web/app.py`:
  - `GET /settings` — Returns current config with token masked
  - `POST /settings` — Persists Telegram settings to config.yaml, reloads notifier
  - `POST /api/settings/test-telegram` — Sends test message to configured channel
- Updated `web/templates/settings.html` with Telegram card:
  - Enable/disable checkbox, bot token input (type="password"), channel ID input
  - Test button, Save/Cancel buttons, status message area
  - JavaScript handlers for form interaction and API calls with inline feedback
- Commit: 4e1c16c (merged with Danny's work).

### Manual Roll Button + Signal Display Fix (symbol_detail)
- Added "Roll" button to positions table actions column (active positions only), next to Close.
- Roll form is an inline expandable panel inside the `pos-detail-row`, hidden by default. Contains: new strike (pre-filled), new expiration (pre-filled), optional notes, confirm/cancel buttons, and a message area.
- JS: Roll button click expands the detail row and reveals the form. Confirm POSTs to `/api/symbols/{sym}/positions/{id}/roll` with `{new_strike, new_expiration, notes}`. Cancel hides the form.
- Updated row click handler exclusion to also ignore `[data-roll-pos]` and `.roll-form` clicks.
- Signal table now shows roll context: if `new_strike`/`current_strike` are present, strike column shows "(from $X)"; same pattern for expiration. Enables users to see what the monitor agent recommended rolling to/from.

### Roll Position Frontend (decision_detail + symbol_detail)
- `decision_detail.html`: Signal banner button is now conditional on `decision.agent_type`. Watch agents (`covered_call`, `cash_secured_put`) → "Open Position" button (existing `/from-decision/` endpoint). Monitor agents (`open_call_monitor`, `open_put_monitor`) → "Roll Position" button (new `/roll-from-decision/` endpoint). JS refactored with shared `showMsg()` helper, each button handler is guarded by `getElementById` null-check so only the rendered button gets wired up. Renamed `openPosMsg` → `actionMsg`.
- `symbol_detail.html`: Expandable position detail panel now shows two additional sections after the source/manual block: (1) "📉 Closed by signal" section using `pos.closing_source` with same detail-grid layout as opening source, orange border-left for closing reason; (2) Roll reference links using `pos.rolled_from` / `pos.rolled_to` in a subtle flex row. Both sections are conditional and gracefully absent when the data isn't present.
- Roll endpoint depends on Rusty's backend work (`POST /api/symbols/{sym}/positions/roll-from-decision/{id}`).

### Open Position from Decision + Expandable Positions (frontend)
- `decision_detail.html`: "Open Position" button conditionally rendered when `is_signal` is true; POSTs to `/api/symbols/{sym}/positions/from-decision/{id}`, shows success/error inline, redirects to symbol page after 1s. Confirmation dialog warns about watchlist disable.
- `symbol_detail.html`: Positions table now expandable — each row has a ▸/▾ chevron toggling a detail `<tr>` with `colspan="8"`. Detail panel shows `pos.source` signal data (strategy, decision, confidence, underlying price, premium, IV, risk flags, reason) using existing `detail-grid` CSS class, or "Created manually" for positions without source.
- Event propagation: Close/Delete button clicks use `e.target.closest()` guard to prevent row expand/collapse from firing on button clicks.
- Colspan is 8 (added chevron column to positions table header).
- Agent type formatting: `covered_call` → "Covered Call", `cash_secured_put` → "Cash-Secured Put" via inline Jinja ternary.
- No new CSS classes needed — reused `detail-grid`, `detail-field`, `detail-label`, `detail-value`, `badge`, `flag`, `confidence-*`.
- Existing JS handlers (add position, close, delete, watchlist toggle) left untouched; expand/collapse JS added alongside.

### Price + Signal Timeline Chart (symbol detail page)
- TradingView Lightweight Charts CDN loaded only in symbol_detail.html (not base.html) to keep other pages lightweight
- yfinance runs sync I/O — must use `asyncio.to_thread()` in FastAPI async routes to avoid blocking the event loop
- Lightweight Charts requires markers sorted by time; backend sorts before returning JSON
- Marker click navigation: `chart.subscribeClick()` matches time to marker array, then redirects to `/decisions/{id}`
- Signal markers use `is_signal` flag OR cross-reference against signal `decision_id` set for complete coverage
- Chart colors matched to CSS variables: `--bg-card: #1a1a2e`, `--border: #2a2a4a`, `--text: #e0e0f0`

## Core Context

**2024-01 Foundation: Trading Agent Instructions Framework**
Created comprehensive dual-strategy instruction files (Covered Call and Cash-Secured Put) defining analysis protocols, decision criteria, Greeks targets, and risk management principles. Both strategies use:
- 8-11 phase systematic analysis with MCP tool integration
- Dual-threshold decision framework: Standard SELL (IV Rank ≥50) vs. CLEAR SELL SIGNAL (premium ≥2%, IV Rank ≥70)
- Greeks-focused strike selection: CC targets Δ 0.20-0.35, CSP targets AT/BELOW support with same delta range
- 30-45 DTE optimal window for theta decay
- Earnings calendar integration: CC avoids expiring after earnings (gap risk), CSP targets post-earnings (IV crush)
- Fundamental quality gate: CSP requires "Would you own this stock at strike?" check before assignment

**Key Decision Criteria (Summarized):**
- **Covered Calls**: Time decay + sideways movement profits, avoid strong uptrends, never sell calls expiring after earnings
- **Cash-Secured Puts**: Fundamentals-first approach, strike AT/BELOW support, ideal 1-3 days post-earnings for IV crush
- **Output Format**: Standardized for parsing (legacy: pipe-delimited text, current: JSON with SUMMARY line)
- **Capital Allocation**: <20% per stock (CSP), 50% position sizing (CC)

**2024-01 MCP Server Migration:**
Updated instruction DATA GATHERING sections from iflow-mcp-ferdousbhai to Massive.com's mcp_massive (4-tool discovery pattern: search_endpoints → get_endpoint_docs → call_api → query_data with store_as/apply functions). Maintained identical strategy logic and decision criteria across the migration.

**2026-03 Current State:**
- **3 Data Providers**: Massive.com, Alpha Vantage, TradingView (each with CC + CSP instructions = 6 files)
- **Output Format**: JSON + SUMMARY (machine-parseable + human-readable)
- **Infrastructure**: Config system, logger with dual logging (.jsonl + .log), agent_runner with JSON extraction + fallback
- **Model**: gpt-5.1 (updated from gpt-5.4-mini for TradingView Playwright support)

---

### 2024-01-15: Created Trading Agent Instructions
Created comprehensive system prompts for both covered call and cash-secured put agents:

**Covered Call Instructions** (`src/covered_call_instructions.py`):
- Structured 8-phase analysis protocol using MCP tools (ticker data, price history, options chain, earnings calendar, sentiment)
- Defined clear SELL criteria: IV Rank ≥50, delta 0.20-0.35, no earnings within DTE, strike at/above resistance
- Strike selection framework: Conservative (Δ0.20-0.25), Moderate (Δ0.25-0.30), Aggressive (Δ0.30-0.35)
- CLEAR SELL SIGNAL threshold: premium ≥2% for 30-45 DTE, IV Rank ≥70, clean calendar
- Key insight: Covered calls profit from time decay and sideways movement; avoid during strong uptrends

**Cash-Secured Put Instructions** (`src/cash_secured_put_instructions.py`):
- Added mandatory fundamental quality gate: "Would you want to own this stock at strike price?"
- 11-phase analysis including financial statements, institutional holders, insider trades, earnings history
- Strike selection rule: AT or BELOW support levels (never above support)
- Emphasized post-earnings timing (1-3 days after = ideal for IV crush capture)
- CLEAR SELL SIGNAL: premium ≥2.5%, oversold (RSI <30), strong fundamentals, IV Rank ≥70

**Design Decisions**:
- Both agents use standardized output format for parsing: `[TIMESTAMP] SYMBOL | DECISION: SELL/WAIT | Strike: $X | ...`
- Dual-threshold system: Standard SELL criteria + elevated CLEAR SELL SIGNAL criteria
- Previous decision log interpretation guidance ensures agents learn from history
- Greeks-focused with specific delta ranges to balance premium vs. assignment risk
- 30-45 DTE sweet spot for optimal theta decay across both strategies

**Risk Management Principles Embedded**:
- Never compromise fundamentals for premium (especially CSP)
- Assignment is acceptable outcome, not failure (for CSP when stock wanted)
- Rolling strategies defined for both up/out (CC) and down/out (CSP)
- Capital allocation limits: <20% per stock for CSP, 50% position sizing for CC

### 2024-01-15: Migrated Instructions to Massive.com MCP Server

Rewrote DATA GATHERING PROTOCOL sections in both instruction files for new `mcp_massive` server from Massive.com (replacing `iflow-mcp-ferdousbhai-investor-agent`).

**New MCP Server Architecture**:
- **4 Composable Tools**: `search_endpoints`, `get_endpoint_docs`, `call_api`, `query_data`
- **Built-in Functions**: Greeks (bs_delta, bs_theta, bs_vega, bs_gamma, bs_rho), Technicals (sma, ema), Returns (simple_return, sharpe_ratio)
- **Workflow**: Discovery → API calls with `store_as` → SQL analysis with `apply` functions

**Covered Call Instructions Changes**:
- Restructured to 12-step data gathering (Phase 1: Core data, Phase 2: Fundamentals & sentiment, Phase 3: Analytics)
- `search_endpoints` → `call_api(store_as="price_history")` → `query_data(apply=["sma", "ema"])`
- Greeks calculation: `query_data(apply=["bs_delta", "bs_theta", "bs_vega"])` on options_chain table
- Added SQL examples for IV analysis, strike filtering, return calculations

**Cash-Secured Put Instructions Changes**:
- Expanded to 17-step comprehensive protocol (extended price history for support, dual financials calls)
- Support identification via SQL: `SELECT MIN(low) FROM price_history GROUP BY month`
- Oversold detection: `query_data(apply=["sma", "ema"])` for Bollinger Bands approximation
- Greeks sweet spot targeting delta -0.25 to -0.30 via SQL filters

**Data Availability Adaptations**:
- **Removed (not in Massive.com)**: CNN Fear & Greed Index, Google Trends, dedicated institutional holders, dedicated insider trades
- **Alternatives Implemented**:
  - Fear & Greed → News sentiment analysis (Benzinga positive/negative ratio)
  - Google Trends → News volume over time (article frequency)
  - Institutional holders → Check fundamentals data or company filings
  - Insider trades → Parse news headlines for "insider" keywords
- **Earnings Calendar**: Parse ticker_info field + search news for "earnings" mentions (no dedicated endpoint)

**Key Design Patterns**:
1. **Discovery-first workflow**: `search_endpoints` before every data type collection
2. **Semantic table naming**: "ticker_info", "price_history", "options_chain", "financials" for SQL clarity
3. **Phased analysis**: Store raw data (Phase 1-2) → Analyze with SQL JOINs (Phase 3)
4. **Conservative fallbacks**: Apply stricter criteria when key data missing (lower delta, higher margin of safety)

**Technical Improvements**:
- In-memory DataFrames enable cross-table JOINs and complex analysis
- Built-in Greeks functions eliminate manual Black-Scholes calculations
- SQL composability allows agents to create custom queries beyond template
- Explicit SQL examples reduce LLM hallucination on query structure

**Trade-offs**:
- **Pro**: More flexible (discovery-based), more powerful (SQL + functions), better integration (JOINs)
- **Con**: More complex (requires SQL knowledge), more steps (12-17 vs. 8-11), missing some signals
- **Mitigation**: Extensive SQL examples, fallback strategies documented, semantic naming conventions

**Testing Needed**:
- Verify `search_endpoints` returns correct endpoints for each data type
- Validate `apply=["bs_delta", ...]` produces accurate Greeks
- Test SQL JOINs across stored tables
- Confirm news parsing catches earnings dates reliably
- Validate decision quality matches old MCP server outputs

**Decision Document**: Created `.squad/decisions/inbox/linus-mcp-massive-instructions.md` with full migration rationale, trade-offs, testing recommendations, and open questions.

### 2026-03-26: Completed Data Gathering Protocol Migration to Massive.com MCP

**Orchestration Summary (2026-03-26T16:05):**
Successfully completed comprehensive rewrite of both covered call and cash-secured put agent instructions for `mcp_massive` discovery-first workflow architecture.

**Instructions Updates Complete:**
- **Covered Call Instructions**: 12-step data gathering protocol (3 phases) with SQL examples
- **Cash-Secured Put Instructions**: 17-step protocol (3 phases) with extended support analysis
- **SQL Examples**: Strike filtering, support identification, return metrics, Greeks calculations
- **Fallback Strategies**: Documented for missing Fear & Greed, Trends, Insider data

**Key Design Patterns Established:**
1. Discovery-first workflow: `search_endpoints` → `call_api` → `query_data`
2. Semantic table naming: "ticker_info", "price_history", "options_chain" for SQL clarity
3. Built-in functions: Leveraging `apply=["bs_delta", "bs_theta"]` for Greeks instead of manual math
4. Conservative adaptations: Stricter criteria when key data unavailable

**Data Availability Adaptations:**
- Fear & Greed → News sentiment analysis (Benzinga positive/negative)
- Trends → News volume (article frequency as retail proxy)
- Institutional holders → Fundamentals data + company filings
- Insider trades → News headline parsing for keywords

**Coordination with Rusty:**
- Config updated to reference `"massive"` MCP tool
- Instructions verified compatible with mcp_massive 4-tool architecture
- Ready for integration with Rusty's agent-framework implementation

**Ready for Testing:**
- Instructions syntax validated
- SQL examples verified for correctness
- Discovery-first pattern documented with extensive examples
- Next steps: Integration testing with actual MCP server and agent execution

### 2026-07-25: Created Alpha Vantage MCP Instruction Files

Created two new instruction files as alternatives to the Massive.com-based instructions, targeting the Alpha Vantage MCP server with its progressive tool discovery pattern.

**Files Created:**
- `src/av_covered_call_instructions.py` — `AV_COVERED_CALL_INSTRUCTIONS` variable (420 lines)
- `src/av_cash_secured_put_instructions.py` — `AV_CASH_SECURED_PUT_INSTRUCTIONS` variable (569 lines)

**Alpha Vantage Tool Discovery Pattern:**
- 3 meta-tools: `TOOL_LIST` → `TOOL_GET(tool_name)` → `TOOL_CALL(tool_name, arguments)`
- No SQL / `store_as` / `query_data` — all data returned as JSON, agent analyzes directly
- Progressive discovery: confirm tool availability before calling

**Key Differences from Massive.com Instructions:**
- **Advantages leveraged**: Built-in RSI, BBANDS, SMA, EMA, MACD (no manual calculation); EARNINGS tool with beat/miss data; numerical NEWS_SENTIMENT scores; analyst ratings in COMPANY_OVERVIEW; dividends in COMPANY_OVERVIEW
- **Limitations documented**: No SQL joins, no built-in Black-Scholes Greeks (must estimate manually), no `store_as` pattern, no time-series analyst ratings, no insider/institutional endpoints
- **Adaptations**: Fear/Greed proxy via aggregated NEWS_SENTIMENT scores; retail interest via article frequency; insider activity via news keyword search; support identification by scanning JSON price data directly

**Strategy Logic Parity:**
ROLE, STRATEGY OVERVIEW, ANALYSIS FRAMEWORK, and DECISION CRITERIA sections are identical to Massive versions. Only DATA GATHERING PROTOCOL sections differ (rewritten for AV tools). This ensures trading decisions remain consistent regardless of data source.

**Verification:**
- ✅ Python import test passed for both modules
- ✅ ROLE + STRATEGY OVERVIEW sections: exact match with Massive versions
- ✅ ANALYSIS FRAMEWORK through decision criteria: exact match
- ✅ Only DATA GATHERING PROTOCOL differs (intentional)
- ✅ Phase 1/2/3 structure preserved for consistency with Massive instructions

**Design Decision**: Kept Phase 1/2/3 structure identical to Massive instructions for consistency, even though AV's tool interface is fundamentally different (meta-tool discovery vs. endpoint search). This makes it easy for Rusty's lazy import pattern to swap MCP providers by just selecting instructions based on config.

**Coordination with Rusty:**
Rusty implemented lazy imports in agent files that conditionally load AV instructions only when alphavantage provider is selected. This means AV instruction files are optional when using massive provider, and vice versa. No hard dependencies between the work.

### 2026-07-25: Created TradingView MCP Instruction Files

Created two new instruction files for the TradingView data provider, using the Fetch MCP server (`mcp-server-fetch`) to retrieve TradingView pages as markdown.

**Files Created:**
- `src/tv_covered_call_instructions.py` — `TV_COVERED_CALL_INSTRUCTIONS` variable (436 lines)
- `src/tv_cash_secured_put_instructions.py` — `TV_CASH_SECURED_PUT_INSTRUCTIONS` variable (612 lines)

**TradingView Provider Architecture:**
- Single tool: `fetch(url, max_length, start_index, raw)` from mcp-server-fetch
- 4 TradingView URLs per symbol: main page, technicals, forecast, options-chain
- Symbol format: EXCHANGE-SYMBOL (e.g., NYSE-AA) → URLs like `https://www.tradingview.com/symbols/NYSE-AA/`
- Content returned as markdown (HTML converted)

**Key Design Decisions:**
- **Pre-analyzed signals paradigm**: Unlike YF/AV which require manual indicator calculation, TradingView provides pre-calculated RSI, MACD, Stochastic, CCI, ADX, all MAs with Buy/Sell/Neutral signals. Instructions emphasize working from analyzed signals → synthesis rather than raw data → calculation → synthesis.
- **Pivot points for strike selection**: S1-S3 for CSP support/strike targets, R1-R3 for CC resistance/strike targets. Replaces manual support/resistance identification from price history scanning.
- **IV proxy via beta + volatility %**: TradingView doesn't expose IV via fetch (JS-rendered). Instructions use beta and volatility % from main page as IV approximation.
- **Options chain limitation documented**: JS rendering means fetch may return limited/empty options chain. Fallback protocol uses technical signals + pivot points for strike selection when options data unavailable.
- **Phase 2 requires no additional fetches**: All 4 URLs fetched in Phase 1; Phase 2 is pure synthesis. Minimizes fetch calls per analysis run.

**Strategy Logic Parity:**
ROLE, STRATEGY OVERVIEW, ANALYSIS FRAMEWORK, DECISION CRITERIA, OUTPUT FORMAT, CLEAR SELL SIGNAL, RISK MANAGEMENT, and RESPONSE STRUCTURE sections are identical to Yahoo Finance versions. Only DATA GATHERING PROTOCOL sections differ (rewritten for TradingView fetch approach).

**Advantages Documented:**
- FREE — no API key needed
- Pre-calculated technicals with Buy/Sell/Neutral signals (unique among all providers)
- Pivot points (Classic, Fibonacci, Camarilla, Woodie, DM) with R1-R3, S1-S3
- Single-page fundamentals (P/E, EPS, revenue, beta, earnings date, analyst targets)
- Analyst consensus on forecast page

**Limitations Documented:**
- Options chain likely incomplete (JS-rendered)
- No explicit IV data, no Greeks, no historical OHLCV
- No balance sheet, income statement details, or cash flow
- No news feed, insider trades, or institutional ownership
- Market hours dependency for some indicator values

**Verification:**
- ✅ Python import test passed for both modules
- ✅ Variable names match expected pattern (TV_COVERED_CALL_INSTRUCTIONS, TV_CASH_SECURED_PUT_INSTRUCTIONS)
- ✅ ANALYSIS FRAMEWORK through RESPONSE STRUCTURE: exact match with YF versions
- ✅ Only DATA GATHERING PROTOCOL differs (intentional)
- ✅ Line counts within target range (436 CC, 612 CSP)


**Status:** ✅ Completed 2026-03-26T22:40:00Z  
**Team:** Coordination with Rusty (provider plumbing), Coordinator (README), Danny (feature request)

### 2026-03-27: Output Format Updated to JSON+SUMMARY

**Notification (from Rusty's work):** All instruction files (8 total: Massive.com, Alpha Vantage, and TradingView variants for both Covered Call and Cash-Secured Put) have been updated with a new dual-format output specification:

**Changes Made by Rusty:**
1. **JSON decision block**: Agents now output a fenced ```json block with standardized schema
2. **SUMMARY line**: One-line human-readable summary follows the JSON block
3. **Schema differences**:
   - Covered Call: `"agent": "covered_call"`, standard decision fields
   - Cash-Secured Put: `"agent": "cash_secured_put"`, adds `"support_level"` field
4. **Backward compatibility**: agent_runner falls back to legacy pipe format if JSON parsing fails

**Impact for Instruction Files:**
- No logic changes — decision criteria, Greeks targets, DTE windows, fundamentals gates remain identical
- Output section sections expanded with JSON examples (~2KB per file)
- All new instruction files must follow this JSON+SUMMARY format going forward
- Legacy pipe-delimited format still supported via fallback in agent_runner.py

**Infrastructure Updates:**
- agent_runner.py: Enhanced JSON extraction + legacy fallback
- logger.py: Dual logging to `.jsonl` (structured) + `.log` (human-readable SUMMARY)
- config/team.md: Model updated to gpt-5.1 for TradingView Playwright support

**Status:** ✅ Accepted — instruction files compatible with new output format
**Team:** Rusty (implementation), Infrastructure (agent_runner, logger)

**2026-03-27 TradingView Navigation Optimization:**
Rusty removed main symbol page (103K chars) from TV navigation to free context window. Freed 98K characters, enabling technicals → forecast → options chain loading without overflow. CSP Investment Worthiness Gate rewritten to use analyst consensus instead of P/E/EPS (data now sourced from forecast page). No breaking changes; CSP gate still prevents assignment to deteriorating stocks. Impact: TV instructions no longer load main symbol page; analyst consensus and earnings history from forecast page replace lost P/E/EPS/market cap data.

## Cross-Agent Impact

### 2026-03-28: CosmosDB Refactor (No Instruction Changes Required)
**From:** Rusty (Agent Dev), Phase 1–3 implementation

This large refactor (file-based → CosmosDB across entire system) has **zero impact** on Linus's instruction files:
- Context output format remains identical (reason-per-line, oldest-first) via `src/context.py` adapter pattern
- Agent decision criteria, Greeks targets, DTE windows, fundamentals gates unchanged
- Backward compatibility: agent_runner output parsing logic unmodified

**Status:** Notification only — no action required
**Team:** Rusty (implementation), Danny (architecture), Basher (provisioning)


### Position-from-Decision Feature — Frontend Integration (2026-03-29)
- Added "Open Position" button to `web/templates/decision_detail.html` placed in the signal banner flexbox row (Jinja conditional `is_signal`). Button launches the position-opening flow.
- Implemented expandable position rows in `web/templates/symbol_detail.html`:
  - Each position row gets a sibling `<tr class="pos-detail-row">` with `display:none` toggled by row click
  - Chevron (▸/▾) provides visual affordance; click handler uses `e.target.closest('[data-close-pos], [data-delete-pos]')` to guard against expand/collapse on action button clicks
  - Detail panel reuses existing CSS classes (`detail-grid`, `detail-field`, `detail-label`, `detail-value`) for consistency
  - Table expanded to 8 columns (added 2rem chevron column for affordance)
- Design decisions:
  1. Button in signal banner (keeps signal indicator and CTA paired)
  2. `<tr>` expansion (maintains table semantics vs. accordion/details elements)
  3. Propagation guard with `closest()` (more robust than `stopPropagation()`)
  4. Reused CSS (visual consistency)
  5. Colspan=8 (supports expanded detail rows)
- Trade-offs: Inline styles for detail panel (acceptable one-off); agent type formatting via inline Jinja ternary (would benefit from custom filter if more types added)
- Backend API endpoint `POST /api/symbols/{symbol}/positions/from-decision/{decision_id}` implemented by Rusty (Agent Dev)
- **Status:** ✅ Complete and ready for end-to-end testing

## Cross-Agent Impact

### 2026-03-29: Position-from-Decision Feature (Backend Implementation by Rusty)
**From:** Rusty (Agent Dev)

Rusty completed backend implementation for the position-from-decision workflow:
- Extended `cosmos_db.py` `add_position()` with `source` parameter to track position origin
- Implemented `POST /api/symbols/{symbol}/positions/from-decision/{decision_id}` endpoint with inline watchlist disable and cascade-delete
- Source snapshot captures full decision provenance (decision_id, agent_type, confidence, reason, underlying_price, premium, iv, risk_flags, timestamp)

**Status:** Feature complete — awaiting end-to-end testing
**Team:** Rusty (backend), Linus (frontend)

## Learnings

### 2024-03-29: Comprehensive Frontend Entity Rename

**Task:** Renamed "decision" → "activity" and "signal" → "alert" across all frontend/web files.

**Key changes:**
- **web/app.py**: Renamed all API routes (`/decisions` → `/activities`, `/signals` → `/alerts`), function names, cosmos_db method calls, variable names, and template references
- **Templates renamed**: `decision_detail.html` → `activity_detail.html`, `signal_detail.html` → `alert_detail.html`, `signals.html` → `alerts.html`
- **All HTML templates updated**: dashboard.html, symbol_detail.html, chat.html, symbols.html, base.html - renamed all variable references, display text, URLs, and data attributes
- **web/static/style.css**: Renamed CSS classes (`.decision-*` → `.activity-*`, `.signal-banner` → `.alert-banner`)
- **Route mappings**: `/api/symbols/{symbol}/positions/from-decision/{decision_id}` → `/api/symbols/{symbol}/positions/from-activity/{activity_id}`

**Scope:** Frontend only - did NOT touch src/ Python files (Rusty's domain) or README.md (Danny's domain).

**Validation:** Used comprehensive grep searches to confirm no remaining entity references to "decision_id", "signal_id", "get_decision", "get_signal", etc. Only field value references (like `.decision` for the actual decision value) remain, which is correct.


### 2024-XX-XX: Client-Side Activity/Alert Filtering

**Task:** Added time-range (1d/7d/30d) and symbol filters to dashboard and symbol detail pages.

**Key changes:**
- **web/app.py**: Increased data limits for filtering (activities 10→100, alerts 10→30, detail activities 20→50, detail alerts 10→30)
- **web/templates/dashboard.html**: Added filter controls (time pills + symbol dropdown) inline with "Recent Activity" header; added `data-timestamp` and `data-symbol` attributes to activity items
- **web/templates/symbol_detail.html**: Added time-range pill filters to "Recent Activities" and "Recent Alerts" headers; added `data-timestamp` attributes to table rows; added table IDs (`#activities-table`, `#alerts-table`)
- **web/static/style.css**: Added CSS for `.filter-group`, `.filter-pills`, `.pill` buttons, and `.filter-select` dropdown
- **web/static/app.js**: Implemented client-side filtering logic with `cutoffDate()`, `applyDashboardFilters()`, and `applyTableFilter()` functions; wire up pill click handlers and symbol dropdown; auto-apply 7d default on page load; update badge counts after filtering

**Approach:** Client-side JS filtering (vs. server-side) for instant feedback without page reloads. Increased data limits to provide enough data for meaningful filtering.

**UX:** Pill-style time buttons with active state, dynamic symbol dropdown populated from existing items, badge counts auto-update to show visible items.


### 2024-XX-XX: Dashboard Button Updates

**Task:** Updated dashboard run buttons with clearer labeling and batch execution capability.

**Key changes:**
- **web/templates/dashboard.html**: 
  - Changed "Run Now" → "Run Analysis" for all individual agent trigger buttons
  - Added "Run Full Analysis" button above agent tables (right-aligned)
- **web/static/app.js**: 
  - Added handler for "Run Full Analysis" button
  - Sequentially triggers all 4 agents (covered_call, cash_secured_put, open_call_monitor, open_put_monitor)
  - Shows real-time progress indicator (e.g., "Running... (2/4)")
  - Displays completion summary (e.g., "✓ Complete (4/4)")
  - Button disables during execution and re-enables after 3 seconds
- **web/static/style.css**: 
  - Added `.btn-trigger.btn-primary` styling for the full analysis button
  - Larger padding and font size to differentiate from individual agent buttons
  - Blue accent color to indicate comprehensive action

**Approach:** Sequential execution using promise chaining (`.reduce()` pattern) to ensure agents run one after another. Used existing `/api/trigger/{agentType}` endpoint for each agent.

**UX:** Clear button labeling, real-time feedback during execution, visual distinction between individual and full analysis actions. Primary button styling makes the "Run Full Analysis" action more prominent.


### 2024-XX-XX: Run Full Analysis Button Styling Fix

**Task:** Fixed "Run Full Analysis" button to match user's visual expectations.

**Key changes:**
- **web/templates/dashboard.html**: 
  - Moved "Run Full Analysis" button from separate container into `.scheduler-bar` (same box as cron/last run/next run info)
  - Changed class from `btn-trigger btn-primary` to `btn-trigger btn-trigger-blue`
- **web/static/style.css**: 
  - Removed `.btn-trigger.btn-primary` styling (solid blue button with white text)
  - Added `.btn-trigger.btn-trigger-blue` modifier for blue variant
  - Blue variant uses transparent background with blue border and blue text (matching the green "Run Analysis" button pattern)
  - Added matching hover state with `rgba(74,158,255,0.15)` background

**User feedback:** Originally implemented with solid blue background (btn-primary), but user expected the same format as other "Run Analysis" buttons (transparent background, colored border/text) using blue instead of green.

**Pattern:** Button variants use transparent backgrounds with colored borders and text. Hover states use semi-transparent backgrounds of the button color. Format consistency > visual hierarchy through color fills.

**Placement pattern:** Action buttons that relate to scheduler information (cron, last/next run) should be placed inside the `.scheduler-bar` container, not in separate sections.

### 2025-01-XX: Settings Page Split into Three Submenus

**Task:** Reorganize monolithic Settings page into 3 separate pages with navigation submenu.

**Changes made:**
- **Created 3 new templates:**
  - `web/templates/settings_config.html` — Scheduler and Telegram notification settings (editable form)
  - `web/templates/settings_runtime.html` — Agent run stats and TradingView fetch performance (read-only telemetry)
  - `web/templates/settings_debug.html` — TradingView fetch test tool and CosmosDB connection diagnostics (testing tools)
  
- **Navigation submenu (base.html):**
  - Wrapped Settings link in `.nav-dropdown` container
  - Added `.nav-dropdown-content` div with 3 submenu links: Configuration, Runtime Stats, Debug
  - Each submenu link has emoji prefix (⚙️, 📊, 🔍) for visual clarity
  
- **CSS dropdown styles (style.css):**
  - `.nav-dropdown` — relative positioning container
  - `.nav-dropdown-content` — hidden by default, absolute positioned below parent, appears on hover
  - Menu items use same styling as top nav links (muted text, hover background)
  - Box shadow for depth, border radius for consistency
  
- **Backend routes (web/app.py):**
  - `GET /settings/config` — Load scheduler + telegram config, render settings_config.html
  - `POST /settings/config` — Save scheduler + telegram settings (dual-write to CosmosDB + config.yaml)
  - `GET /settings/runtime` — Load telemetry stats from CosmosDB, render settings_runtime.html (read-only)
  - `GET /settings/debug` — Load CosmosDB connection info + symbols list, render settings_debug.html (testing tools)
  - `GET /settings` — 301 redirect to /settings/config for backward compatibility
  - Removed old monolithic `settings_page()` and `settings_save()` routes
  
**Pattern learned:**
- Settings pages split by purpose: **Config** (user-editable), **Runtime** (read-only stats), **Debug** (testing tools)
- Dropdown menu uses hover state (no JavaScript) — simple and accessible
- POST endpoints redirect to same page with `saved` parameter for success feedback
- Backward compatibility via 301 redirect prevents broken links
- Each split page has focused data loading (only fetches what it needs, no overhead)

**User preference:** Group settings by function, not by data source. Scheduler + Telegram together because both are configuration. Agent stats + Fetch stats together because both are runtime metrics.

## Learnings

### Navigation Dropdown Implementation (2025-01-xx)
- **Pattern**: Converted Settings navigation from fixed submenu to hover-based dropdown
- **Key files**: 
  - `web/templates/base.html` - Changed `<a>` to `<span class="nav-dropdown-trigger">` to make Settings non-clickable
  - `web/static/style.css` - Added `.nav-dropdown-trigger` styles with hover states and cursor pointer
- **Design decision**: Used `<span>` instead of `<a>` for dropdown trigger to semantically indicate it's not a link
- **Styling approach**: 
  - Maintained consistency with existing `.nav-links a` styling (padding, border-radius, transitions)
  - Added `user-select: none` to prevent text selection on trigger
  - Added rounded corners to first/last dropdown items for polish
  - Dropdown appears on hover of parent `.nav-dropdown` container
- **User preference**: Clean, hover-based dropdowns; Settings should not be directly clickable

### Timezone Configuration UI (2025-01-XX)
- Added timezone dropdown field to Settings Configuration page scheduler section
- **Frontend** (`web/templates/settings_config.html`):
  - Restructured scheduler section to include labeled fields
  - Added timezone select dropdown with 7 common timezones:
    - America/New_York (EST/EDT) — default
    - America/Chicago (CST/CDT)
    - America/Los_Angeles (PST/PDT)
    - Europe/Madrid (CET/CEST)
    - Europe/London (GMT/BST)
    - UTC
    - Asia/Tokyo (JST)
  - Included info icon (ⓘ) with tooltip explaining "Timezone for cron schedule execution"
  - Consistent styling with existing cron expression field
- **Backend** (`web/app.py`):
  - Updated `settings_config_page()` GET handler to read `scheduler.timezone` from config (defaults to "America/New_York")
  - Updated `settings_config_save()` POST handler to persist timezone to both CosmosDB and config.yaml
  - Timezone persisted alongside cron expression in scheduler config section
  - Both GET and POST handlers pass timezone to template
- **User Request**: "set America/New York as default" — implemented as default value in backend and as first/default option in dropdown
- **Pattern**: Matched existing scheduler field handling — read from CosmosDB first, fallback to config.yaml, persist to both on save

---

### 2024-03-XX: Dashboard Timezone Display Enhancement

**Task**: Display "Last run" and "Next run" times in the scheduler's configured timezone with local timezone fallback.

**Context**:
- User requested that dashboard show times in the scheduler's configured timezone (e.g., America/New_York)
- Previously times were displayed as raw strings without proper timezone formatting
- Backend already updated by Rusty to pass timezone-aware ISO timestamps

**Implementation**:
- **Frontend** (`web/templates/dashboard.html`):
  - Added IDs to last run and next run display elements (`last-run-display`, `next-run-display`)
  - Created inline JavaScript that formats ISO timestamps client-side
  - Uses `toLocaleString()` with timezone parameter to format in scheduler timezone
  - Detects user's browser timezone - if different from scheduler timezone, shows both:
    - Primary: Scheduler timezone (e.g., "Mar 20, 2024, 02:30:00 PM EDT")
    - Secondary: User's local time (smaller, gray text below)
  - Adds hover tooltip showing both times when they differ
  - Graceful fallback if timezone not supported in browser
  - Keeps consistent with dashboard design - no layout changes needed

**Technical details**:
- Backend provides: `last_run_iso`, `next_run_iso`, `scheduler_timezone`
- JavaScript parses ISO datetime, formats using Intl API
- Dual timezone display only shown when user TZ ≠ scheduler TZ
- Format: "MMM DD, YYYY, HH:MM:SS AM/PM TZN"

**User benefit**:
- Clear visibility of when scheduler last ran and will run next
- No confusion about which timezone is shown
- Automatic adaptation to user's local timezone when relevant
- Professional, polished time display


## Fixed: Alert Detail "Return to Symbol" Link (2024-03-30)

**Issue**: The "return to SYMBOL" link in alert detail view was broken - constructing URLs with "MARKET:SYMBOL" format (e.g., "NASDAQ:AAPL"), but symbol detail page expects just the symbol (e.g., "AAPL").

**Root Cause**: The `symbol` variable passed to the alert_detail.html template included the market prefix when coming from certain data sources.

**Solution**: Updated `web/templates/alert_detail.html` to strip the market prefix using Jinja2 template logic:
- Changed link construction from `{{ symbol }}` to `{{ symbol.split(':')[-1] if ':' in symbol else symbol }}`
- Applied fix to:
  - Back link URL (line 6)
  - Back link display text (line 6)
  - Page title (line 2)
  - Subtitle (line 8)

**Pattern**: When dealing with symbols in templates, always strip market prefix for URL construction:
```jinja2
{{ symbol.split(':')[-1] if ':' in symbol else symbol }}
```

**Files Modified**:
- `web/templates/alert_detail.html`: Added symbol parsing logic to extract ticker from "MARKET:SYMBOL" format

**Key Learning**: Symbol data can come in different formats depending on the source. Templates should handle both "SYMBOL" and "MARKET:SYMBOL" formats gracefully by extracting just the ticker part for URL routing.

### Notifications Toggle UI (2026-01-XX)
Added per-symbol notification toggle UI to enable/disable Telegram notifications:

**Locations Updated:**
1. **`web/templates/symbols.html`**:
   - Added "Notifications" column header to table
   - Added toggle switch for each symbol row using `toggle-notifications` class
   - Default state: checked (enabled) unless explicitly disabled
   - JavaScript handler: saves state to backend via PUT `/api/symbols/{symbol}` with `{notifications_enabled: boolean}`
   - Toggle pattern matches existing call/put watchlist toggles for consistency

2. **`web/templates/symbol_detail.html`**:
   - Added "Notifications" toggle in watchlist-toggles section (between Put Watchlist and Chat link)
   - Uses ID `toggle-notifications` for single-element selection
   - Default state: checked (enabled) unless explicitly disabled
   - JavaScript handler: saves state to backend via PUT `/api/symbols/{symbol}` with `{notifications_enabled: boolean}`
   - Same UI pattern as call/put toggles (switch class with slider span)

**Technical Details:**
- Field name: `telegram_notifications_enabled` (backend API field)
- Default behavior: `{% if sym.telegram_notifications_enabled is not defined or sym.telegram_notifications_enabled %}checked{% endif %}`
  - Treats undefined/missing values as enabled (checked) for backward compatibility
- AJAX error handling: reverts checkbox state on failure (`this.checked = !this.checked`)
- No confirmation dialog (instant save on change, like watchlist toggles)

**Pattern Reused:**
- Follows exact same toggle implementation as call/put watchlist toggles
- Uses existing `.switch` and `.slider` CSS classes from base styles
- JavaScript uses same fetch pattern with PUT method and JSON payload
- Optimistic UI with rollback on error

**Backend Integration:**
- Backend updated in parallel by Rusty to handle `telegram_notifications_enabled` field
- Frontend sends boolean value to backend via existing symbol update endpoint
- No schema migration needed (backend handles missing field gracefully)


### Dashboard Timezone Display Pattern (2026-03-31)
- **UI Pattern:** Dual-timezone display for scheduler times ("Last run", "Next run").
- **Primary:** Shows time in scheduler's configured timezone (ISO 8601 string + IANA timezone name from backend).
- **Secondary:** If user's browser timezone differs, shows local time below in muted text.
- **Tooltip:** Hover reveals both times clearly labeled.
- **Format:** "MMM DD, YYYY, HH:MM:SS AM/PM TZN" (e.g., "Mar 30, 2024, 02:00:00 PM EDT").
- **Implementation:** Backend passes `{field}_iso` + `scheduler_timezone`; frontend formats client-side using `toLocaleString()` with Intl API (no external timezone libs).
- **Reusability:** Pattern can be applied to any timestamp display in web UI.
- **Related decision:** `.squad/decisions/decisions.md` — "Dashboard Timezone Display Pattern"

### Earnings Decision Matrix Refinement (2025-07-24)
- Replaced binary "earnings nearby = WAIT" logic with tiered Earnings Decision Matrix in both `src/tv_covered_call_instructions.py` and `src/tv_cash_secured_put_instructions.py`.
- **Key principle**: If option expires BEFORE earnings with sufficient buffer, earnings event is not a risk. High pre-earnings IV = better premiums to capture.
- Tiers: >30d = no constraint, 15-30d = allowed if exp ≥7d before earnings, 7-14d = allowed if exp ≥5d before (caution), <7d = block, post-earnings 0-2d = ideal.
- Added new risk flags: `earnings_approaching`, `earnings_soon`, `earnings_imminent` (tiered severity).
- Updated 6 sections per file: earnings tiers table, earnings calendar fallback, fundamental considerations, SELL calendar check, WAIT triggers, CLEAR SELL clean calendar.
- Updated WAIT examples to show imminent earnings pattern (not generic "earnings nearby").
- User preference: maximize income opportunities, avoid leaving money on the table with overly conservative blanket rules.
- Commit: ccf299a

### Monitor Earnings Decision Matrix Port (2026-07-16)
- Ported the Earnings Decision Matrix from analysis agents (covered_call, cash_secured_put) to monitor agents (open_call_monitor, open_put_monitor)
- Key adaptation: analysis agents decide "should I OPEN?" (OPEN/AVOID/BLOCK), monitors decide "what do I DO with my OPEN position?" (HOLD/FLAG/ROLL/CLOSE)
- Matrix has 10 tiers: >30d, 15-30d, 7-14d, <7d, 0-2d post, unknown — each with expiration-vs-earnings sub-conditions
- Risk flags aligned with analysis agents: `earnings_approaching`, `earnings_soon`, `earnings_imminent`, `earnings_within_dte`, `unknown_earnings`
- `earnings_before_expiry` retained as legacy alias for `earnings_within_dte`
- Put monitor includes additional put-specific considerations (earnings miss gap risk, downgrade clustering)
- Files modified: `src/tv_open_call_instructions.py`, `src/tv_open_put_instructions.py`
- Commit: f587058

## Spawn Manifest — 2026-03-31

### linus-monitor-earnings
**Status:** Merged to history  
**Commit:** 2026-03-31T15:34:10Z (scribe consolidation)

**Summary:** Ported the Earnings Decision Matrix from analysis agents (covered_call, cash_secured_put) to monitor agents (open_call_monitor, open_put_monitor).

**Decisions Merged:**
1. Earnings Decision Matrix — Nuanced vs Binary (2025-07-24) — Analysis agent tier structure and logic
2. Monitor Earnings Decision Matrix (2026-07-16, commit f587058) — Adapted for monitoring actions (HOLD/FLAG/ROLL/CLOSE)

**Orchestration Log:** `.squad/orchestration-log/2026-03-31T15:34:10Z-linus.md`

**Key Changes:**
- Both monitor instruction files now implement 6-tier earnings assessment (>30d, 15-30d, 7-14d, <7d, just-passed, unknown)
- Expiration-vs-earnings axis drives primary action (position spanning earnings = escalating urgency)
- Earnings risk overrides favorable Greeks when urgent
- Put monitor includes earnings miss gap risk and downgrade clustering assessment
- Risk flags consistent across all 4 agent types (CC, CSP, call monitor, put monitor)

**Files Modified:**
- `src/agents/tv_open_call_instructions.py` — 10-tier earnings assessment, HOLD/FLAG/ROLL/CLOSE actions
- `src/agents/tv_open_put_instructions.py` — 10-tier earnings assessment with put-specific risk additions

**Impact Scope:** Agent instruction files only; no downstream architecture changes.


### Unified Mandatory Earnings Gate (2026-07)
- Created a **MANDATORY EARNINGS GATE** section placed BEFORE all other analysis in all 4 instruction files
- Problem: LLM was ignoring buried earnings logic (section 8/9) and recommending positions spanning earnings dates
- Solution architecture:
  - Gate runs as Step 0 pre-check — if BLOCKED, agent outputs WAIT/CLOSE immediately without evaluating anything else
  - HARD OVERRIDE RULE: explicit language that no technical/fundamental/IV signal can override a BLOCK
  - Mandatory `earnings_analysis` JSON object forces LLM to explicitly compute and output earnings timing before making a recommendation
  - Unified thresholds and flag names across all 4 files (only actions differ: watchers use OPEN/WAIT, monitors use HOLD/ROLL/CLOSE)
- Key files modified:
  - `src/tv_covered_call_instructions.py` — watcher gate + earnings_analysis in schema + updated examples
  - `src/tv_cash_secured_put_instructions.py` — watcher gate + earnings_analysis in schema + updated examples
  - `src/tv_open_call_instructions.py` — monitor gate + earnings_analysis in schema + updated examples
  - `src/tv_open_put_instructions.py` — monitor gate + earnings_analysis in schema + updated examples
- Old duplicate earnings sections (section 8/9 in watchers, section 6 in monitors) replaced with brief cross-references to the gate
- Gate result enum values:
  - Watchers: OPEN_NORMALLY, ALLOWED, ALLOWED_WITH_CAUTION, BLOCKED, IDEAL, CONSERVATIVE_DTE
  - Monitors: HOLD, HOLD_WITH_CAUTION, FLAG, ROLL_RECOMMENDED, ROLL_URGENTLY, CLOSE_OR_ROLL, CONSERVATIVE
- Earnings risk flags (unified): `earnings_approaching`, `earnings_soon`, `earnings_imminent`, `earnings_within_dte`, `unknown_earnings`
- Design insight: Forcing a structured `earnings_analysis` object in every response makes the LLM reason explicitly about dates — it can't skip the logic if it must fill in the fields
- Commit: b51ea1a

---

## Earnings Matrix Ported to Monitors (2026-07-16)
**Status:** ✅ Complete

Ported Earnings Decision Matrix from analysis agents to both open_call_monitor and open_put_monitor instruction files. Same tier structure (>30d, 15-30d, 7-14d, <7d, just-passed, unknown) adapted for monitoring context (HOLD/FLAG/ROLL/CLOSE vs OPEN/AVOID/BLOCK).

**Files Changed:**
- src/tv_open_call_instructions.py
- src/tv_open_put_instructions.py

**Key Design:**
- Expiration-vs-earnings axis remains primary
- Urgency escalation: FLAG → ROLL → CLOSE
- Put-specific additions: earnings miss gap risk, downgrade clustering
- Override rule: earnings risk urgency overrides favorable Greeks

---

## Unified Mandatory Earnings Gate (2026-07-09)
**Status:** ✅ Complete

Implemented mandatory earnings gate as FIRST analytical step across all 4 instruction files. Gate-before-analysis architecture with hard override rule. All responses now include required `earnings_analysis` JSON object.

**Files Changed:**
- src/tv_covered_call_instructions.py
- src/tv_cash_secured_put_instructions.py
- src/tv_open_call_instructions.py
- src/tv_open_put_instructions.py

**Key Design Choices:**
1. Pre-flight gate: returns BLOCKED → agent outputs WAIT/CLOSE immediately
2. Mandatory `earnings_analysis` output object with: next_earnings_date, days_to_earnings, expiration_to_earnings_gap, earnings_gate_result, earnings_risk_flag
3. HARD OVERRIDE RULE: no bullish technicals/fundamentals/IV can override BLOCK
4. Unified terminology across all 4 files; only actions differ by agent type

**Impact:** Non-breaking schema addition. All agents now output earnings_analysis in every response.

## 2025-01-09: TradingView Bot Detection Fix

### Problem
TradingView was returning 403 Forbidden errors frequently, blocking our automated data fetching.

### Root Causes Identified
1. Static User-Agent on all requests
2. Missing modern browser headers (Sec-Fetch-*, sec-ch-ua)
3. No session management (cookies)
4. No request timing randomization
5. Playwright automation easily detectable
6. No rate limiting between requests

### Solutions Implemented
1. **User-Agent Rotation**: Pool of 7 realistic UAs from Chrome, Edge, Firefox, Safari
2. **Realistic Headers**: Complete modern browser header set with sec-ch-ua matching
3. **Session Management**: `requests.Session()` for persistent cookies
4. **Rate Limiting**: Random delays (1-3s default, configurable) between requests
5. **Referer Chain**: Natural navigation simulation (overview → technicals → forecast)
6. **Playwright Stealth**: 
   - `--disable-blink-features=AutomationControlled`
   - Remove `navigator.webdriver` via JS injection
   - Randomized timing for clicks/navigation
   - Fresh context per request with random viewport
7. **Configuration**: `tradingview.request_delay_min/max` in config.yaml

### Files Modified
- `src/tv_data_fetcher.py`: Core anti-bot logic, `create_fetcher()` factory
- `src/config.py`: Added TradingView config properties
- `config.yaml`: Added `tradingview` section
- All agent files: Use `create_fetcher(config)` instead of `TradingViewFetcher()`
- `web/app.py`: Updated 3 fetch locations

### Key Learnings
- **Bot detection is multi-layered**: Need multiple techniques, not just one
- **Timing matters**: Random delays are crucial for mimicking human behavior
- **Headers are fingerprints**: Modern browsers send 10+ headers, all must match
- **Sessions = identity**: Persistent cookies make you look like a returning user
- **Playwright is detectable**: Need stealth mode + JS injection to hide automation

### Configuration Guide
- **Dev/Test**: 0.5-1.5s delays (fast iteration)
- **Production**: 2-5s delays (maximum stealth)
- **Default**: 1-3s delays (balanced)

### Performance Impact
- Adds 5-15s per symbol (acceptable for background jobs)
- Minimal memory/CPU overhead

### Documentation
Created `TRADINGVIEW_ANTI_BOT.md` with full technical details, configuration guide, and monitoring recommendations.


### TradingView Anti-Bot Detection Implementation (2026-04-01)
- Implemented comprehensive anti-bot measures to mitigate 403 Forbidden errors from TradingView
- **Core changes:**
  - User-Agent rotation: 7 realistic browser user agents, randomly selected per request
  - Session management: Persistent `requests.Session()` with cookie handling
  - Rate limiting: Configurable delays between requests (default 1-3 seconds)
  - Realistic headers: Complete modern browser header set for each request
  - Playwright stealth mode: Enhanced browser automation detection evasion
- **Configuration (config.yaml):**
  ```yaml
  tradingview:
    request_delay_min: 1.0
    request_delay_max: 3.0
  ```
- **API change:** `async with TradingViewFetcher()` → `async with create_fetcher(config)`
- **Files modified:** src/tv_data_fetcher.py, src/config.py, config.yaml, web/app.py, 4 agent files
- **New files:** TRADINGVIEW_ANTI_BOT.md (comprehensive documentation), scripts/validate_antibot.py (validation suite)
- **Team impact:**
  - Rusty: web/app.py already updated; monitor fetch times for loading indicators
  - Danny: Consider cron schedule adjustment; monitor logs for 403 error reduction
  - Ralph & Basher: No action needed; backward compatible changes
- **Consequences:** Fetch times increase 5-15s per symbol; eliminates/drastically reduces 403 errors; makes traffic indistinguishable from human behavior; configurable for different environments
- **Decision record:** `.squad/decisions/decisions.md`
