# Linus — History

## Project Context
- **Project:** options-agent
- **User:** dsanchor
- **Stack:** Python, Microsoft Agent Framework, Azure Foundry (gpt-5.4-mini)
- **MCP:** iflow-mcp_ferdousbhai_investor-agent 1.6.3
- **Description:** Two periodic trading agents for covered call and cash-secured put sell signals. Local runtime, configurable polling, stock symbols from files, decision logs, sell signal alerts.

## Learnings

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

