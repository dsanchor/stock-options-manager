# Squad Decisions

## Active Decisions

### 4. MCP Server Migration to Massive.com (Agent Instructions)
**Date:** 2026-03-26  
**Author:** Linus (Quant Dev)  
**Status:** ✅ Completed  
**Impact:** Team-wide (affects agent instructions and data gathering workflow)

#### Context

Migrated both covered call and cash-secured put agent instructions from the old `iflow-mcp-ferdousbhai-investor-agent` MCP server to the new `mcp_massive` from Massive.com. The old server had specific tool calls like `get_ticker_data()`, `get_price_history()`, `get_cnn_fear_greed_index()`, etc. The new Massive.com MCP server has a fundamentally different architecture with 4 composable tools and built-in analytical functions.

#### Key Design Decisions

**1. Discovery-First Workflow**
- **Decision:** Structure data gathering protocol around `search_endpoints` → `call_api` → `query_data` progression
- **Rationale:** The new MCP server is endpoint-agnostic; agents discover what they need rather than knowing tool names upfront
- **Impact:** Instructions now guide LLM through discovery phase before data collection

**2. In-Memory DataFrames with Meaningful Names**
- **Decision:** Use `store_as` parameter consistently with semantic table names (e.g., "price_history", "options_chain", "financials")
- **Rationale:** Enables SQL JOINs and cross-analysis in later steps
- **Pattern:** Phase 1: Store raw data tables → Phase 2: Store supplementary context → Phase 3: Query and analyze with SQL

**3. Built-in Functions for Greeks & Technicals**
- **Decision:** Leverage `apply` parameter extensively for Black-Scholes Greeks and technical indicators
- **Functions Used:** Greeks: `bs_delta`, `bs_gamma`, `bs_theta`, `bs_vega`, `bs_rho`; Technicals: `sma`, `ema`; Returns: `simple_return`, `cumulative_return`, `sharpe_ratio`
- **Rationale:** Avoid manual calculations; use optimized built-in functions for accuracy and speed

**4. Data Availability Adaptations**
- **Removed:** CNN Fear & Greed Index, Google Trends, Dedicated institutional holders endpoint, Dedicated insider trades endpoint
- **Alternatives:** Fear & Greed → News sentiment analysis; Trends → News volume; Institutional holders → Fundamentals; Insider trades → News parsing
- **Rationale:** Maintain decision quality with available data; apply conservative criteria when key signals missing

**5. Earnings Calendar Strategy**
- **Challenge:** No dedicated earnings calendar endpoint in Massive.com
- **Solution:** Multi-source: Check ticker_info for next earnings date field, parse news headlines for "earnings" mentions
- **Impact:** Instructions emphasize importance of earnings timing but acknowledge data may require manual validation

**6. Phased Data Gathering Structure**
- **Decision:** Maintain 3-phase structure (Core Data → Context → Analytics) with enhanced SQL capabilities
- **Rationale:** Logical progression mirrors decision-making process
- **Enhancement:** Phase 3 now includes explicit SQL examples for JOINs and `apply` functions

**7. Conservative Stance When Data Missing**
- **Decision:** Apply stricter criteria when key data unavailable (lower delta, higher margin of safety)
- **Examples:** If insider data unavailable → require stronger fundamentals; If Fear & Greed unavailable → focus on IV Rank; If earnings unclear → default to WAIT unless >60 days buffer
- **Rationale:** Incomplete information = higher risk; compensation required

#### Technical Implementation

**Covered Call Instructions Changes:**
- Phase 1: 4 steps (ticker details, price history with technicals, options chain, dividends)
- Phase 2: 5 steps (fundamentals, analyst ratings, news, sentiment proxy via news, retail interest via news volume)
- Phase 3: 3 steps (IV analysis, Greeks calculations, return metrics)
- Total: 12 data-gathering steps + 1 consolidation (granular and composable)

**Cash-Secured Put Instructions Changes:**
- Phase 1: 5 steps (ticker details, extended price history, dual financials, options chain, dividends)
- Phase 2: 6 steps (analyst ratings, news, earnings history via news, market movers, fear proxy, retail proxy)
- Phase 3: 6 steps (support via SQL, oversold conditions, Greeks, IV analysis, premium calculations, insider parsing)
- Total: 17 data-gathering steps + 1 consolidation (comprehensive analysis)

**SQL Examples Added:**
- Support identification: `SELECT MIN(low) FROM price_history` (CSP)
- Strike filtering: `SELECT * FROM options_chain WHERE delta BETWEEN 0.20 AND 0.35` (CC)
- Sentiment proxy: `SELECT sentiment FROM news GROUP BY sentiment`
- Greeks calculation: `SELECT ... apply=["bs_delta", "bs_theta"]`

#### Trade-offs

**Pros:**
1. More flexible: Discovery-based approach adapts to API changes
2. More powerful: SQL + built-in functions enable complex analysis
3. Better data integration: In-memory tables allow JOINs and cross-analysis
4. Composable: 4 simple tools combine for unlimited use cases

**Cons:**
1. More complex: Requires LLM to understand SQL and compose multi-step queries
2. More steps: 12-17 steps vs. 8-11 single tool calls (though more granular control)
3. Data gaps: Missing some signals (Fear & Greed, Google Trends, Insider Trades)
4. Discovery overhead: Each run requires `search_endpoints` calls

**Mitigations:**
- Provide extensive examples in instructions
- Document fallback strategies for missing data
- Emphasize semantic table naming for easier SQL composition
- Include explicit SQL templates for common queries

#### Success Criteria

- ✅ Instructions compile without syntax errors
- ✅ All available data gathering steps documented
- ✅ SQL examples tested for correctness
- ✅ Fallback strategies defined for missing data
- ⏳ Agent successfully gathers all available data
- ⏳ Agent makes same quality decisions as with old MCP server
- ⏳ No degradation in signal accuracy or timing

---

### 5. Multi-Provider MCP Configuration with Provider Switching
**Date:** 2026-07-25  
**Decider:** Rusty (Agent Dev)  
**Status:** ✅ Completed  
**Impact:** Team-wide (enables flexible provider selection without code changes)

#### Context

The project initially deployed with `mcp_massive`, then added Alpha Vantage as alternative. Rather than maintaining two separate codebases, we needed a single config-driven approach to switch providers at runtime without code changes.

#### Decision

Implemented provider-based MCP configuration structure:
```yaml
mcp:
  provider: "massive"  # or "alphavantage"
  massive:
    command: "mcp_massive"
    env_key: "MASSIVE_API_KEY"
  alphavantage:
    command: "mcp_alphavantage"
    env_key: "ALPHAVANTAGE_API_KEY"
```

#### Key Design Decisions

1. **Prune inactive providers before env var substitution**
   - Removes non-active provider config sections before resolving environment variables
   - Prevents crash when user only sets API key for selected provider
   - Rationale: User shouldn't need to set all provider keys, only the active one

2. **Lazy instruction imports in agent files**
   - Instruction imports happen inside `async def run()` method, not at module level
   - Conditional logic selects instructions based on `config.mcp_provider`
   - Rationale: AV instruction files don't need to exist for Massive mode

3. **Dynamic MCP tool naming and env key**
   - `AgentRunner` takes `mcp_name` and `env_key` as constructor parameters
   - No more hardcoded "massive" or "MASSIVE_API_KEY"
   - Rationale: Single runner implementation serves all providers

#### Implementation

**Files Updated:**
1. `config.yaml` — Provider selector + per-provider sections
2. `src/config.py` — `mcp_provider`, `mcp_env_key` properties; `_prune_inactive_providers()`
3. `src/agent_runner.py` — Dynamic `mcp_name` and `env_key` parameters
4. `src/covered_call_agent.py` — Lazy provider-specific instruction import
5. `src/cash_secured_put_agent.py` — Lazy provider-specific instruction import
6. `src/main.py` — Pass provider settings to AgentRunner

#### Trade-offs

| Aspect | Pro | Con |
|--------|-----|-----|
| Single config file | Easy to switch providers | Can't use multiple providers in one run |
| Lazy imports | AV files optional for Massive mode | Slightly more complex agent logic |
| Prune before substitute | No required env vars for inactive providers | Inactive config discarded at load time |

#### Consequences

**Positive:**
- Users can select provider in config without code changes
- Supports future providers without architectural changes
- Instruction sets can evolve independently per provider

**Neutral:**
- Requires one env var per active provider (similar to before)
- Runtime cost of lazy imports negligible

#### Verification

- ✅ Config loads correctly with provider selector
- ✅ Pruning removes inactive sections before env var resolution
- ✅ Lazy imports only trigger on provider match
- ✅ AgentRunner accepts dynamic names and env keys
- ✅ Old config format detected with helpful error message

---

### 6. Alpha Vantage MCP Instruction Files (Strategy Logic Parity)
**Date:** 2026-07-25  
**Author:** Linus (Quant Dev)  
**Status:** ✅ Completed  
**Files:** `src/av_covered_call_instructions.py` (420 lines), `src/av_cash_secured_put_instructions.py` (569 lines)  
**Impact:** Team-wide (enables trading with Alpha Vantage data source)

#### Context

The project established comprehensive trading instructions for Massive.com MCP server. When Alpha Vantage was selected as alternative provider, we needed parallel instructions that:
- Keep all strategy logic and decision criteria identical
- Only adapt the data gathering protocol to AV's 3-meta-tool architecture (TOOL_LIST → TOOL_GET → TOOL_CALL)
- Leverage AV's unique advantages (built-in technicals, earnings data, sentiment scores)

#### Decision

Created parallel instruction files maintaining 100% strategy parity while optimizing data gathering for AV's tool interface.

#### Key Design Decisions

1. **Preserve all decision criteria identically**
   - Same SELL thresholds (IV Rank, delta ranges, DTE windows)
   - Same strike selection rules (CC: above support, CSP: at/below support)
   - Same output format for signal parsing
   - Rationale: Trading logic should not vary by data source

2. **Phase 1/2/3 structure preserved**
   - Covered Call: 3 phases (core data → context → analytics)
   - Cash-Secured Put: 3 phases (extended core → comprehensive context → analytics)
   - Rationale: Consistent naming makes provider swapping intuitive

3. **Leverage AV advantages for efficiency**
   - **Built-in technicals:** RSI, Bollinger Bands, MACD, SMA, EMA (vs. Massive's manual calculation)
   - **Earnings calendar:** Dedicated EARNINGS tool with beat/miss (vs. Massive's news parsing)
   - **Sentiment scores:** Numerical NEWS_SENTIMENT (vs. Massive's text analysis)
   - **Analyst ratings:** Direct COMPANY_OVERVIEW field (vs. Massive's fundamentals search)
   - Rationale: Use native capabilities for clarity and accuracy

4. **Manual adaptation for missing capabilities**
   - **Greeks:** No built-in Black-Scholes; instructions provide estimation guidance
   - **Joins:** No SQL; agent must synthesize across JSON objects
   - **Insider data:** No dedicated endpoint; instructions guide keyword search in news
   - Rationale: Incomplete data requires conservative criteria, not failure

#### Technical Implementation

**Covered Call Instructions (420 lines):**
```
ROLE + STRATEGY OVERVIEW
  ↓
ANALYSIS FRAMEWORK (Greeks, DTE, earnings)
  ↓
DATA GATHERING (TOOL_LIST → TOOL_GET → TOOL_CALL progression)
  Phase 1: Ticker, price history, options chain, dividends
  Phase 2: Fundamentals, analyst ratings, news/sentiment, technicals
  Phase 3: IV analysis, Greeks estimation, return calcs
  ↓
DECISION CRITERIA + OUTPUT
```

**Cash-Secured Put Instructions (569 lines):**
```
ROLE + STRATEGY OVERVIEW
  ↓
ANALYSIS FRAMEWORK (quality gate, DTE, earnings, technicals)
  ↓
DATA GATHERING (TOOL_LIST → TOOL_GET → TOOL_CALL progression)
  Phase 1: Extended core (price for support ID, dual financials, earnings history)
  Phase 2: Comprehensive (analyst, news, sentiment scores, fundamental quality)
  Phase 3: Strike selection (support via JSON scan, oversold via BBANDS/RSI, Greeks estimation)
  ↓
DECISION CRITERIA + OUTPUT
```

#### Trade-offs

| Aspect | Massive.com | Alpha Vantage |
|--------|-------------|---------------|
| Tool discovery | `search_endpoints` keyword search | `TOOL_LIST` + `TOOL_GET` discovery |
| Data aggregation | SQL JOINs across stored tables | Manual JSON synthesis |
| Technical indicators | Manual via `apply=["sma"]` | Built-in RSI, BBANDS, MACD, EMA |
| Greeks calculation | `apply=["bs_delta", "bs_theta"]` | Manual estimation guidance |
| Earnings data | Parse from news | Direct EARNINGS tool |
| Sentiment | Text-based analysis | Numerical NEWS_SENTIMENT scores |
| Institutional holders | Fundamentals or search | COMPANY_OVERVIEW consensus |

**Advantages AV:**
- Simpler tool interface (no SQL needed)
- More reliable earnings data
- Numerical sentiment is faster to analyze
- Built-in technicals reduce LLM hallucination

**Advantages Massive:**
- SQL composability for complex analysis
- Black-Scholes Greeks built-in
- More granular data control

#### Consequences

**Positive:**
- Single strategy logic supports both providers
- Provider swapping is config change only
- AV's built-in capabilities often provide faster/more accurate analysis
- Instruction maintenance: bug fixes apply to both via common sections

**Neutral:**
- AV requires more manual Greeks estimation (acceptable given other advantages)
- More instruction files to maintain (offset by exact copying of common sections)

**Mitigations:**
- Common sections (ROLE, STRATEGY, CRITERIA) identical between versions
- Extensive examples in DATA GATHERING for AV's tool discovery pattern
- Conservative criteria documented for missing signals

#### Verification

- ✅ Both files valid Python (import test passed)
- ✅ ROLE + STRATEGY OVERVIEW: exact match across versions
- ✅ ANALYSIS FRAMEWORK through DECISION CRITERIA: exact match
- ✅ Only DATA GATHERING PROTOCOL differs (intentional, AV-specific)
- ✅ All tool names verified against AV documentation
- ✅ Phase structure mirrors Massive version

#### Coordination

**Depends on:** Rusty's lazy import pattern (selection happens in agent files)  
**Enables:** Agent provider swapping via `config.yaml` change only  
**Documentation:** Common decision rationale in decisions.md; provider-specific details in each instruction file

#### Next Steps

1. **Integration testing:** Verify AV TOOL_LIST discovery works with actual API
2. **Signal quality comparison:** Compare decision logic output vs. Massive
3. **Provider migration:** Document process for users switching providers

---

## Decision: Alpha Vantage Remote MCP Transport

**Date:** 2026-07-25  
**Author:** Rusty  
**Status:** Implemented

### Context
Alpha Vantage now provides a hosted MCP server at `mcp.alphavantage.co` using SSE/streamable HTTP transport. This eliminates the need for a local `uvx marketdata-mcp-server` subprocess.

### Decision
Replaced the local stdio-based Alpha Vantage MCP integration with the remote streamable HTTP endpoint. Added a `transport` field to config to distinguish between stdio (Massive.com) and streamable_http (Alpha Vantage) providers.

### Key Design Choices
1. **Backward compatible** — `transport` defaults to `"stdio"` so Massive.com config needs no changes
2. **Validation split** — stdio providers require `command`+`args`, HTTP providers require `url`
3. **Config-level env substitution preserved** — API key is embedded in the URL via `${ALPHAVANTAGE_API_KEY}` pattern, same env var expansion as before
4. **API key env check still runs** — even though the key is in the URL, we validate the env var exists at runtime to give a clear error message

### Impact
- No local `uvx`/`marketdata-mcp-server` install needed for Alpha Vantage users
- Massive.com workflow unchanged
- `MCPStreamableHTTPTool` from `agent_framework` handles the HTTP transport

---

## Governance

- All meaningful changes require team consensus
- Document architectural decisions here
- Keep history focused on work, decisions focused on direction

---

## Decision: TradingView Provider Plumbing + EXCHANGE-SYMBOL Format

**Date:** 2026-03-26  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented

### Context
Danny requested adding TradingView as a 4th MCP provider and changing the symbol file format from plain tickers (e.g., `AAPL`) to `EXCHANGE-SYMBOL` (e.g., `NASDAQ-AAPL`).

### Decision

#### TradingView Provider
- Uses `mcp-server-fetch` via `uvx` — a generic web-fetch MCP tool, not a finance-specific one.
- No API key required (unlike Massive or AlphaVantage).
- The agent instructions (Linus's domain) will direct the LLM to fetch specific TradingView URLs for analysis.

#### EXCHANGE-SYMBOL Parsing
- Parsing uses `symbol.split('-', 1)` to extract exchange and ticker.
- Backward-compatible: symbols without a dash still work (exchange = "", ticker = full string).
- Decision logs and matching now use the ticker portion only, keeping output clean.

### Alternatives Considered
- Could have used a dedicated TradingView MCP server — none exists as a mature package. The generic fetch server is the right abstraction since Linus's instructions control what URLs are fetched.
- Could have used a tuple/dict format for symbols — plain text `EXCHANGE-SYMBOL` is simpler to maintain and edit by hand.

### Impact
- **Linus must create**: `tv_covered_call_instructions.py` and `tv_cash_secured_put_instructions.py` before the tradingview provider can be activated.
- Existing providers (massive, alphavantage, yahoo) are unaffected.
- Symbol files changed — any external tooling reading these files needs to handle the new format.

---

## Decision: TradingView Instruction File Design

**Date:** 2026-03-26  
**Author:** Linus (Quant Dev)  
**Status:** Implemented  
**Files:** `src/tv_covered_call_instructions.py`, `src/tv_cash_secured_put_instructions.py`

### Context
Added TradingView as a third data provider option (alongside Yahoo Finance and Alpha Vantage). TradingView uses the Fetch MCP server (`mcp-server-fetch`) with a single `fetch` tool to retrieve TradingView web pages as markdown.

### Key Decisions

#### 1. Pre-analyzed signals paradigm
TradingView provides Buy/Sell/Neutral signals already computed for oscillators and MAs. Instructions tell the agent to work from these analyzed signals rather than calculating indicators from raw data. This is a fundamental difference from YF/AV instructions.

#### 2. Pivot points as primary support/resistance
Instead of scanning historical price data for support/resistance (which TradingView fetch doesn't provide as OHLCV), instructions use Classic pivot points S1-S3 (support) and R1-R3 (resistance) for strike selection.

#### 3. IV proxy strategy
Since TradingView's options chain is JS-rendered and may not return IV data via fetch, instructions define beta + volatility % from the main page as IV proxy. High beta + high volatility % = likely elevated IV.

#### 4. Graceful options chain degradation
Instructions include explicit fallback protocol when options chain data is empty: use technical signals for direction, pivot points for strike levels, beta/volatility for IV proxy.

### Impact on Team
- **Rusty**: Will need to add TradingView as a provider option in config.yaml and implement lazy imports for `TV_COVERED_CALL_INSTRUCTIONS` / `TV_CASH_SECURED_PUT_INSTRUCTIONS` in agent files (same pattern as AV).
- **Config**: New provider name `"tradingview"` with MCP tool `"mcp-server-fetch"`.
- **No breaking changes**: Existing YF and AV instruction files are untouched.

### Trade-offs

| Pro | Con |
|-----|-----|
| FREE — no API key | Options chain likely incomplete |
| Pre-calculated technicals | No explicit IV, no Greeks |
| Pivot points built-in | No historical OHLCV data |
| Single-page fundamentals | No balance sheet / cash flow details |
| Fewest fetch calls (4 URLs) | No news feed / sentiment scores |

---

## Decision: Structured JSON Output Format for Decisions

**Date:** 2026-03-27  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  
**Impact:** Team-wide (changes agent output parsing, logging, and instruction format)

### Context

Replaced the pipe-delimited human-readable output format with a machine-parseable JSON schema + SUMMARY line across all 8 instruction files and the agent runner infrastructure.

### Decision

1. **JSON decision block**: Agents output a fenced ```json block with a standardized schema containing all decision fields (symbol, decision, strike, expiration, IV metrics, premium, confidence, risk_flags, etc.)
2. **SUMMARY line**: A one-line human-readable summary immediately after the JSON block
3. **Dual logging**: JSON → `.jsonl` files, SUMMARY → existing `.log` files
4. **Backward compatibility**: agent_runner tries JSON first, falls back to legacy pipe format

### Schema Definition

**Covered Call Decision Block:**
```json
{
  "agent": "covered_call",
  "symbol": "AAPL",
  "decision": "SELL",
  "strike": 175,
  "expiration": "2026-04-17",
  "dte": 21,
  "iv_rank": 72,
  "premium_percent": 2.3,
  "confidence": 0.85,
  "risk_flags": ["near_earnings"],
  "reason": "Strong IV, premium >2%, clean technicals"
}
```

**Cash-Secured Put Decision Block:**
```json
{
  "agent": "cash_secured_put",
  "symbol": "MSFT",
  "decision": "SELL",
  "strike": 410,
  "expiration": "2026-04-17",
  "support_level": 408,
  "dte": 21,
  "iv_rank": 68,
  "premium_percent": 2.8,
  "confidence": 0.90,
  "risk_flags": [],
  "reason": "Support identified at $408, premium strong"
}
```

### Schema Differences

- Covered call: `"agent": "covered_call"` — standard fields
- Cash-secured put: `"agent": "cash_secured_put"` — adds `"support_level"` field

### Trade-offs

- **Pro**: Machine-parseable output enables downstream automation, dashboards, analytics
- **Pro**: SUMMARY line preserves human readability
- **Pro**: `.jsonl` format enables easy batch processing (one JSON per line)
- **Con**: Larger instruction text (~2KB more per file) due to JSON examples
- **Con**: Agent may occasionally produce malformed JSON (fallback handles this)

### Implications for Team

- **Linus**: Instruction files now specify JSON output format — any new instruction files must follow the same schema
- **Basher**: Test cases should verify JSON extraction from agent responses
- **Danny**: Downstream systems can now consume `.jsonl` files for structured decision data
- **Scribe**: README may need updating to document the new output format

---

## User Directive: Model Configuration Change

**Date:** 2026-03-27T09:18:56Z  
**By:** dsanchor (via Copilot)  
**Status:** Implemented in config/team.md

### Context
Updated model configuration from gpt-5.4-mini to gpt-5.1 based on performance observations with TradingView Playwright multi-step tool-calling workflows.

### Directive

**Switch model from gpt-5.4-mini to gpt-5.1**

- **Reason:** gpt-5.1 shows superior performance on multi-step browser instruction sequences (navigate → click → snapshot for options chain data extraction from TradingView)
- **Previous model performance:** gpt-5.4-mini unable to follow complex sequential browser commands reliably
- **gpt-5.1 advantages:** Better instruction following on step-by-step workflows

### Impact

- Applies to all agent instruction files using TradingView provider
- Updated in `config/team.md` model field
- Existing Massive.com and Alpha Vantage workflows unaffected
- Configuration propagates to all agents via team config inheritance

---

## 2. TradingView Navigation Optimization: Remove Main Symbol Page

**Date:** 2026-03-27T09:38:00Z  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  
**Impact:** Team-wide (improves TradingView agent data gathering)

### Context

TradingView Playwright agent was experiencing context window overflow, preventing access to technicals and forecast pages. Root cause analysis showed 4 pages producing 245K total characters:
- Main symbol page: 103K chars ← Problem
- Technicals: 48K chars
- Forecast: 29K chars
- Options chain (expanded): 65K chars

After loading main (103K) + options chain (65K) = 168K, insufficient context remained for technicals and forecast.

### Decision

Remove main symbol page entirely from navigation. Load only 3 pages in optimized order:
1. **Technicals** (48K) — most valuable for technical analysis
2. **Forecast** (29K) — earnings dates, analyst consensus, price targets
3. **Options chain** (65K) — strikes, premiums, IV, Greeks

### Trade-offs

**Lost data (from main page):**
- P/E ratio, EPS, revenue, market cap, beta
- Company description, sector classification
- CSP fundamental quality gate loses detailed financials

**Preserved/Replaced:**
- Current price → Visible in options chain headers and forecast page
- Earnings date → Available on forecast page
- Analyst price targets → Available on forecast page
- Beta/volatility proxy → Replaced with actual IV% from options chain (superior)
- CSP Investment Worthiness Gate → Rewritten to use analyst consensus + earnings history

### Implementation

**Files Changed:**
- `src/tv_covered_call_instructions.py` — Updated navigation, removed main page
- `src/tv_cash_secured_put_instructions.py` — Updated navigation, CSP gate rewrite

**CSP Gate Logic Update:**
```
OLD: if P/E < 30 and EPS_positive and market_cap > 1B → PROCEED
NEW: if analyst_consensus >= 60% (Buy/Hold) and no_surprise_losses_2qtrs → PROCEED
```

Data sources: Analyst consensus and earnings history now sourced from forecast page.

### Quality Assurance

- ✅ Context freed: 245K → 142K (98K reduction)
- ✅ All 3 critical pages now load without overflow
- ✅ CSP gate still prevents assignment to deteriorating stocks
- ✅ No changes to decision logic or Greeks selection
- ✅ Backward compatible (stronger, not weaker)

### Team Implications

- **Linus (Quant Dev):** CSP gate now depends on analyst consensus; adjust backtests referencing P/E
- **Danny (Product):** TradingView instructions now capture analyst targets and earnings dates
- **Basher (Test/Ops):** Verify TV mocks include forecast page earnings history
- **Scribe (Docs):** Update TV data gathering docs in README

---

### 12. User Directive: JSONL-Only Decision/Signal Output

**Date:** 2026-03-27  
**Author:** dsanchor (via Copilot)  
**Status:** Proposed  
**Impact:** Output format simplification

#### Decision

Drop `.log` decision/signal files entirely. Keep only `.jsonl` output for decisions and signals. Update `config.yaml` paths accordingly.

#### Rationale

Single machine-parseable format reduces file management complexity. JSONL is easier to parse and aggregate than multiple file types.

---

### 13. Open Position Monitor Agents

**Date:** 2025-07  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  
**Impact:** New feature — two new agents added to the scheduler

#### Context

Added OpenCallMonitor and OpenPutMonitor agents that track existing short options positions for assignment risk. These complement the existing sell-side agents (CoveredCallAgent, CashSecuredPutAgent).

#### Key Decisions

1. **TradingView-only**: Position monitors only work with the TradingView pre-fetch path. No MCP fallback — these agents have no tool access.
2. **Separate method**: `run_position_monitor_agent()` is a new method on AgentRunner, not a modification to `run_agent()`. The position file format, message template, and signal detection are all different.
3. **Position file format**: `EXCHANGE-SYMBOL,strike,expiration` — one position per line, comments/blanks supported.
4. **Roll signal fields**: Separate `_ROLL_SIGNAL_FIELDS` tuple with fields appropriate for position management (current_strike, current_expiration, new_strike, new_expiration, action) rather than sell signals.
5. **Graceful degradation**: Monitors skip silently when position files are empty/all-commented. Non-TradingView providers get a warning and skip.

#### Files Created/Modified

**Created:**
- `data/opened_calls.txt`, `data/opened_puts.txt` — position data files
- `src/tv_open_call_instructions.py`, `src/tv_open_put_instructions.py` — agent instructions
- `src/open_call_monitor_agent.py`, `src/open_put_monitor_agent.py` — agent wrappers

**Modified:**
- `src/agent_runner.py` — added `_read_positions()`, `_is_roll_signal()`, `_build_roll_signal_data()`, `run_position_monitor_agent()`
- `src/config.py` — added `open_call_monitor_config`, `open_put_monitor_config` properties
- `src/main.py` — imports + scheduler calls for both monitors
- `config.yaml` — new `open_call_monitor` and `open_put_monitor` sections
- `README.md` — architecture, key concepts, output, project structure updated

---

### 14. Re-add TradingView Overview Page as Pre-Fetched Resource

**Author:** Rusty (Agent Dev)  
**Date:** 2025-07  
**Status:** Proposed

#### Context

The overview page (`/symbols/EXCHANGE:TICKER/`) was previously dropped to save context budget (~103K chars for the old accessibility snapshot approach). With the `browser_run_code` + `innerText` extraction method, the page is much smaller and provides valuable fundamental data (P/E, market cap, dividend yield, sector) that the agent previously had to infer indirectly from analyst consensus.

#### Decision

Add `fetch_overview()` as the first pre-fetched resource, using the same `browser_run_code` + `main.innerText` pattern as technicals/forecast. This keeps the page size manageable (innerText is far smaller than accessibility snapshots) while giving the agent direct access to fundamentals.

#### Consequence

- The CSP Investment Worthiness Assessment can now use actual P/E, market cap, and dividend data instead of proxy signals.
- Total pre-fetch count goes from 3 → 4 pages per symbol, adding one browser navigation per symbol.
- If context budget becomes tight again, overview is the first candidate to drop (it was lived without before).

---

### 15. Profit Optimization Signals for Open Position Monitors

**Date:** 2025-07-22  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  
**Impact:** Agent behavior (monitor instruction prompts)

#### Context

The open position monitors (call + put) previously only detected defensive roll scenarios (assignment risk). Users wanted proactive profit optimization — rolling to a tighter strike to collect more premium when conditions are unanimously safe.

#### Decision

Added profit optimization instruction sections to both `tv_open_call_instructions.py` (ROLL_DOWN) and `tv_open_put_instructions.py` (ROLL_UP). Uses a 9-condition unanimous consensus gate — ALL must pass or the decision stays WAIT.

#### Key Design Choices

1. **Instruction-only change**: No schema changes, no `agent_runner.py` changes. ROLL_DOWN/ROLL_UP and `risk_flags` were already fully supported. This validates the architecture — schema is stable, behavior evolves through prompts.

2. **9-condition unanimity gate**: Deep OTM (5%+), very low delta (<0.15), technicals aligned, MAs aligned, no catalysts, analyst sentiment not contrary, low IV, DTE > 14, stable decision history. "No gambling" — one ambiguous indicator = WAIT.

3. **`profit_optimization` risk_flag**: Semantic marker distinguishing "rolling because the position is at risk" from "rolling because I can safely collect more premium." Propagates through existing `_ROLL_SIGNAL_FIELDS` pipeline.

4. **Confidence must be "high"**: If the agent can't say high confidence, it must not recommend the optimization.

#### Trade-offs

- **Conservative by design**: Many valid optimization opportunities will be missed because one indicator is neutral instead of confirmatory. This is intentional — false positives (bad optimization) are far worse than false negatives (missed premium).
- **No new schema fields**: Keeps the signal pipeline simple but means downstream consumers must check `risk_flags` to distinguish profit vs defensive rolls.

---

### 16. README Documentation Structure

**Date:** 2025-07  
**Author:** Rusty (Agent Dev)  
**Status:** Completed

#### Decision

Restructured README to separate "how to run it" (Setup/Running) from "how it works" (How It Works/Key Concepts). Added dedicated sections for:
1. End-to-end execution flow with provider branching
2. Decision vs Signal semantics
3. Pre-fetch architecture rationale
4. Per-symbol context filtering explanation
5. Full annotated config.yaml reference
6. Example JSONL output object

#### Rationale

The README previously covered setup and troubleshooting well but didn't explain _what the system does_ or _why_ it's designed this way. A new contributor couldn't understand the pre-fetch architecture, the decision/signal distinction, or the context injection system without reading source code. These are the core design decisions that define the project.

#### Implications

- README is now the single source of truth for system behavior — keep it updated when architecture changes
- Config reference in README mirrors actual config.yaml structure — update both together

---

### 17. Use browser_run_code for TradingView Technicals & Forecast

**Date:** 2025-07  
**Author:** Rusty  
**Status:** Implemented

#### Context

The TradingView agent uses Playwright MCP to scrape 3 pages. `browser_navigate` returns full accessibility snapshots: technicals ~48K chars, forecast ~38K chars, options chain ~37K+65K expanded. Total ~188K chars was overwhelming the model context, causing it to report "pages failed to load."

#### Decision

Use `browser_run_code` (Playwright JS execution) for technicals and forecast pages. This navigates to the page AND extracts `innerText` in a single call, returning ~3K and ~2.4K chars respectively (15-16x reduction). Options chain stays on `browser_navigate`+`browser_click`+`browser_snapshot` because it needs accessibility tree element refs for interactive clicking.

#### Trade-offs

- **Pro:** ~80K chars freed per analysis run — model no longer chokes on context
- **Pro:** `innerText` contains identical data in cleaner tab-separated format
- **Pro:** Single tool call per page vs navigate+wait+snapshot
- **Con:** `browser_run_code` returns plain text, not structured accessibility tree — cannot use element refs for clicking (not needed for these pages)
- **Con:** If TradingView changes DOM structure (e.g., removes `<main>` tag), the fallback to `document.body` still works but may include more noise

#### Affected Files

- `src/tv_covered_call_instructions.py`
- `src/tv_cash_secured_put_instructions.py`

---

### 18. TradingView Pre-Fetch Architecture

**Date:** 2025-07-17  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  
**Commit:** 9bca215

#### Context

The LLM agent unreliably executes 3+ sequential Playwright browser tool calls — it skips pages, fabricates navigation errors, or ignores tool-calling instructions. Multiple instruction-based fixes were attempted (reordering pages, innerText extraction via browser_run_code, reducing snapshot size) — none solved the fundamental problem.

#### Decision

Pre-fetch ALL TradingView data deterministically in Python, then pass it to the agent as text. The agent receives NO browser tools — it only analyzes.

#### Implementation

1. **New module `src/tv_data_fetcher.py`**: `TradingViewFetcher` class uses the same Playwright MCP tools (browser_run_code, browser_navigate, browser_click, browser_snapshot) but driven from Python, not the LLM.
2. **`src/agent_runner.py`**: Branches on `mcp_provider == "tradingview"` — pre-fetch path creates ChatAgent with no tools; all other providers use existing MCP-tool flow unchanged.
3. **TV instruction files**: Phase 1 rewritten from "gather data via browser tools" to "review pre-fetched data". All `browser_*` references removed. Phase 2 analysis logic, trading rules, output format, decision criteria unchanged.

#### Trade-offs

- **Pro**: 100% reliable data fetching — Python deterministically loads all 3 pages every time
- **Pro**: Agent context is smaller and cleaner — only data + analysis instructions, no tool-call overhead
- **Pro**: Non-tradingview providers completely unaffected
- **Con**: Agent cannot adaptively explore pages (e.g., try different expirations) — but this was unreliable anyway
- **Con**: Pre-fetch always loads all 3 pages even if one would suffice — acceptable overhead

#### Impact

- Covered call and CSP agents using TradingView provider should now consistently analyze all 3 data sources (technicals, forecast, options chain) instead of randomly skipping 1-2 pages.

---

### 19. Web Dashboard Architecture

**Date:** 2025-07-28  
**Author:** Rusty (Agent Dev)  
**Status:** Completed

#### Context

Added a web dashboard for the options agent system — a separate entry point (`run_web.py`) using FastAPI + Jinja2 templates with a dark trading theme.

#### Key Decisions

1. **Separate entry point, shared data files**: Web dashboard (`run_web.py`) and scheduler (`python -m src.main`) run independently. Both read the same JSONL logs and data files — no database layer needed.

2. **Raw YAML config loading**: The web app reads `config.yaml` directly via `yaml.safe_load()` instead of using `src.config.Config`, which requires MCP environment variables. The web app only needs the Azure endpoint (for chat) and scheduler cron expression.

3. **No build step**: Vanilla HTML/CSS/JS with custom dark-theme CSS. No npm, no bundler, no CSS framework dependency.

4. **JSONL as the database**: All dashboard data comes from reading JSONL log files and `data/*.txt` files on every request. Acceptable for the current log sizes; would need indexing if logs grow to millions of lines.

5. **Chat uses direct OpenAI API**: The chat endpoint uses `openai.AzureOpenAI` with `AzureCliCredential` — same auth pattern as the agent runner but without the agent framework overhead. Context is the last 20 decisions per log file.

6. **Hot-reload confirmed**: `_read_symbols()` and `_read_positions()` in `agent_runner.py` read from disk on every call inside `run_agent()` / `run_position_monitor_agent()`. No caching — edits via the settings page take effect on the next scheduler tick with zero code changes.

#### Trade-offs

- Reading JSONL on every request is fine for current scale but won't scale to huge logs. If needed, add a lightweight caching layer or SQLite index later.
- No authentication on the web dashboard — acceptable for local/internal use. Add auth middleware if exposing to the internet.




---
---
---




### 20. Consolidated Entry Point (`run.py`)

**Date:** 2025-07
**Author:** Rusty (Agent Dev)

## Context
The project had two separate entry points — `python -m src.main` for the scheduler and `python run_web.py` for the web dashboard. Users had to start them independently in separate terminals.

## Decision
Consolidate into a single `python run.py` that runs both web dashboard and scheduler. The scheduler runs as a daemon thread managed by FastAPI's lifespan context. CLI flags (`--web-only`, `--scheduler-only`, `--port`) provide fine-grained control.

## Key details
- Lifespan attached via `app.router.lifespan_context` — avoids modifying `web/app.py`.
- `OptionsAgentScheduler.run(install_signals=False)` when threaded — signal handlers are main-thread-only.
- `run_web.py` kept as backwards-compat shim delegating to `run.py --web-only`.
- Host/port read from `config.yaml` `web:` section; `--port` flag overrides.

## Files changed
- `run.py` (new) — unified entry point
- `src/main.py` — `run()` accepts `install_signals` param; `__main__` block suggests `run.py`
- `run_web.py` — now delegates to `run.py --web-only`
- `README.md` — updated Running section

---

### 21. Always use signal_log for dashboard and signal views

**Date:** 2025-07-22
**Author:** Rusty (Agent Dev)
**Status:** Implemented

## Context
Dashboard counts for position monitors were reading from `decision_log`, which includes WAIT decisions. This inflated signal counts (e.g., 3 WAITs shown as 3 signals when actual actionable signals were 0).

## Decision
All dashboard counts, signal list pages, and signal detail pages now read exclusively from `signal_log`. The `decision_log` is only used for:
1. "Recent Activity" feed on the dashboard (which shows all events)
2. "Recent Decisions" context section on the signals list page
3. Backing decisions on the signal detail page (correlated by timestamp)

## Impact
- Dashboard signal counts now accurately reflect actionable signals only
- Signals list page gains a "Recent Decisions" section for analysis context
- No changes to how logs are written — only how they're read for display

---

### 22. Remove non-TradingView MCP providers

**Author:** Rusty (Agent Dev)
**Date:** 2025-07-23
**Status:** Implemented

## Context

The project supported four MCP data providers (Massive.com, Alpha Vantage, Yahoo Finance, TradingView) with per-provider instruction files, config branching, and transport selection. In practice, TradingView + Playwright pre-fetch is the only provider that works reliably — LLMs cannot drive multi-step browser/tool workflows, and the other providers' MCP servers had various limitations.

## Decision

Remove all non-TradingView providers. TradingView via Playwright is the sole data source.

## Changes

- **Deleted:** 6 instruction files (`av_*`, `yf_*`, generic `covered_call_instructions.py`, `cash_secured_put_instructions.py`)
- **Simplified:** `config.yaml` MCP section flattened (no `provider` key, no per-provider sub-sections)
- **Simplified:** `config.py` — removed provider selection, pruning, transport/url/env_key properties
- **Simplified:** `agent_runner.py` — removed entire non-TradingView code path (MCP tool creation, HTTP transport, API key validation)
- **Simplified:** Agent wrappers — no provider branching, always use TV instructions
- **Updated:** README — removed multi-provider docs, comparison table, env var setup for removed providers

## Trade-offs

- **Lost:** Ability to switch to Massive/AV/Yahoo without code changes
- **Gained:** ~4100 lines of dead code removed, dramatically simpler config and runtime paths, no unused env var requirements

## Team Implications

- **Linus (Quant Dev):** Only TV instruction files exist now. Any instruction changes go to `tv_*` files.
- **Basher (Test/Ops):** No need to test multiple providers. Playwright container is the only external dependency.
- **Scribe (Docs):** README already updated. No multi-provider docs to maintain.
# Decision: Dashboard Run Button UX

**Date:** 2024-12-XX  
**Author:** Linus (Quant Dev / Frontend Dev)  
**Status:** Implemented  

## Context

The dashboard had "Run Now" buttons for each agent, but users needed:
1. Clearer button labeling (what does "Run Now" actually do?)
2. Ability to trigger all agents at once for comprehensive analysis

## Decision

1. **Button Text Change**: "Run Now" → "Run Analysis"
   - More explicit about what the button does
   - Aligns with the purpose: running analysis, not just "now"

2. **New Full Analysis Button**: Added "Run Full Analysis" button
   - Positioned above agent tables, right-aligned
   - Triggers all 4 agents sequentially (covered_call, cash_secured_put, open_call_monitor, open_put_monitor)
   - Shows progress during execution: "Running... (2/4)"
   - Blue primary styling to distinguish from individual agent buttons

## Implementation

- Sequential execution using promise chaining (not parallel)
- Uses existing `/api/trigger/{agentType}` endpoint
- Real-time progress feedback
- Button disables during execution, re-enables after completion

## Rationale

- **Sequential over Parallel**: Ensures controlled execution order and reduces server load
- **Progress Indicator**: Users can see which agent is currently running
- **Primary Styling**: Visual hierarchy makes it clear this is a comprehensive action
- **Consistent Patterns**: Reuses existing trigger button styles and API endpoints

## Alternatives Considered

1. **Parallel Execution**: Rejected due to potential resource contention
2. **Server-Side Batch Endpoint**: Rejected to keep frontend changes isolated
3. **Modal Dialog**: Rejected as too heavy for a simple batch trigger

## Impact

- **Frontend**: 3 files modified (dashboard.html, app.js, style.css)
- **Backend**: No changes needed (reuses existing endpoints)
- **UX**: Improved clarity and efficiency for users running multiple agents


---

### 8. Button Alignment Fix — Run Full Analysis Button
**Date:** 2025  
**Author:** Linus (Quant Dev / Frontend)  
**Status:** Completed  
**Impact:** UI/UX (visual consistency)

#### Context
The "Run Full Analysis" button was positioned inline with scheduler information (cron, last run, next run) in the `.scheduler-bar` container. Individual "Run Analysis" buttons on each agent card are right-aligned, creating a visual inconsistency.

#### Key Design Decision
Updated `.scheduler-bar` CSS to use flexbox space distribution:
1. Added `justify-content: space-between` — Distributes space evenly, pushing the button to the right
2. Added `align-items: center` — Ensures vertical alignment with scheduler text
3. Added `.scheduler-bar .btn-trigger { margin-left: auto; }` — Ensures button stays right, even with flex-wrap

#### Implementation
- **File Modified:** web/static/style.css
- **HTML Changes:** None (CSS-only solution)
- **Rationale:** Button already had correct CSS classes (`btn-trigger btn-trigger-blue`); solution uses standard flexbox patterns consistent with existing card headers

#### Result
"Run Full Analysis" button now right-aligns within scheduler info bar, matching visual alignment of individual "Run Analysis" buttons on agent cards.

#### Trade-offs
- **Simplicity:** CSS-only approach avoids template changes
- **Consistency:** Uses existing flexbox patterns already in codebase


---

### 9. Chat UI Design System Alignment
**Date:** 2024-03-31  
**Author:** Rusty (Agent Dev)  
**Status:** Completed  
**Impact:** Web UI consistency

#### Context
The dual-mode chat interface (Portfolio Chat + Quick Analysis) was initially implemented with custom CSS styles that didn't match the rest of the application's design system. User feedback indicated the look and feel was inconsistent with dashboard, settings, and other pages.

#### Key Design Decisions

1. **Use Standard Card Components**
   - Replace custom `.mode-option` styles with standard `.card` + `.card-header` structure
   - Use existing design tokens (`var(--bg-input)`, `var(--bg-hover)`, `var(--border)`, `var(--accent-blue)`)
   - Match padding, spacing, and border-radius to other cards in the app

2. **Free Text Input for Market Field**
   - Replace dropdown with text input for flexibility
   - Apply text-transform: uppercase for consistent display
   - Allows users to enter any market/exchange name

3. **Unified Navigation Pattern**
   - Use `.btn-sm` class for all back buttons across both modes
   - Consistent placement in card headers
   - Same "← Back" text pattern throughout

4. **Form Consistency**
   - Use `.hint` class for descriptive text (matches settings pages)
   - Use `.input-field` class for form inputs
   - Match label styling from `settings_config.html`

#### Implementation
- **Files Changed:** `web/templates/chat.html`, `web/static/style.css`
- **Design Tokens Used:** `--bg-card`, `--bg-input`, `--bg-hover`, `--border`, `--accent-blue`, `--text`, `--text-muted`, `--radius`
- **Refactoring:** Removed 30+ lines of unused CSS

#### Result
Standard card-based selection with free text inputs matching app design; all functionality preserved, visual consistency achieved.

#### Trade-offs
- **Flexibility vs Validation**: Free text input allows any market name but sacrifices dropdown validation (acceptable for power users)
- **Simplicity**: CSS reuse reduces code duplication and future maintenance burden

---

### 10. Quick Analysis Button Enable Pattern
**Date:** 2026-03-31  
**Author:** Rusty (Agent Dev)  
**Status:** ✅ Implemented  
**Impact:** Form UX improvements

#### Context
The Quick Analysis mode in `chat.html` has a "Fetch & Analyze" button that requires both `symbol` and `market` inputs. The button was initially enabled, causing UX confusion when clicked without filled fields (would show error instead of preventing click).

#### Decision
Form submission buttons in multi-mode UIs should start disabled and enable dynamically based on required field validation.

#### Implementation
1. **Default State:** Button starts with `disabled` attribute
2. **Validation Function:** `checkFetchButtonState()` checks both fields have trimmed values
3. **Event Listeners:** Attach `input` events (not `keyup`) to catch paste/autofill
4. **Mode Entry Check:** Call validation function when form first displays
5. **Enter Key:** Respect button state (don't submit if disabled)

#### Benefits
- **Immediate Feedback:** Button state reflects form validity in real-time
- **Prevents Errors:** Users can't submit incomplete forms
- **Navigation Safe:** Handles back/forward, mode switching, pre-filled values
- **Accessible:** Visual disabled state is also functional (no click handler run)

#### Pattern for Team
When adding form-based flows with required fields:
```javascript
// 1. Start button disabled
<button id="submitBtn" disabled>Submit</button>

// 2. Create validation function
function checkFormValidity() {
    const isValid = requiredField1.value.trim() && requiredField2.value.trim();
    submitBtnEl.disabled = !isValid;
}

// 3. Attach to inputs
field1El.addEventListener('input', checkFormValidity);
field2El.addEventListener('input', checkFormValidity);

// 4. Check on display
function showForm() {
    formEl.style.display = 'block';
    checkFormValidity(); // handles pre-filled values
}

// 5. Respect in Enter handlers
fieldEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !submitBtnEl.disabled) {
        submit();
    }
});
```

#### Files Changed
- `web/templates/chat.html`

#### Related Decisions
- Chat UI Design System Alignment (2026-03-31) — established form field patterns
- Standard `.btn` disabled styles in `web/static/style.css`

---

### 11. Quick Analysis Chat — Centralized Instruction Reuse for Put/Call Analysis
**Date:** 2026-04-01  
**Decider:** Rusty (Agent Dev)  
**Status:** ✅ Implemented  
**Impact:** Chat feature enhancement, Agent instruction reuse

#### Context
Quick Analysis chat feature extension. Previously, Quick Analysis just fetched data and started a blank chat. User wanted the first message to be the same quality analysis that monitoring agents provide—not a generic greeting.

#### Decision
Quick Analysis chat now provides automatic first analysis using the same centralized monitoring agent instructions (`TV_OPEN_CALL_INSTRUCTIONS` / `TV_OPEN_PUT_INSTRUCTIONS`) based on user-selected option type (Call/Put).

#### Implementation Details

**Frontend Changes** (`web/templates/chat.html`)
- Three-input form: Symbol + Market + Option Type (required dropdown)
- Automatic analysis trigger on successful fetch
- State flag `awaitingFirstAnalysis` to track flow
- UI shows "Analyzing for Call/Put options..." while waiting

**Backend Changes** (`web/app.py`)
- `/api/chat/fetch-symbol`: Accept and return `option_type` parameter
- `/api/chat`: Handle `first_analysis` flag
  - When `true`: Import appropriate instruction file and use as system prompt
  - When `false`: Use standard chat system prompt
- Instructions imported at runtime: `from tv_open_{call|put}_instructions import TV_OPEN_{CALL|PUT}_INSTRUCTIONS`

**Centralized Instruction Files** (Unchanged)
- `src/tv_open_call_instructions.py` — Used by `open_call_monitor` agent and Quick Analysis (call)
- `src/tv_open_put_instructions.py` — Used by `open_put_monitor` agent and Quick Analysis (put)

#### Benefits
1. **Consistency** — Quick Analysis users get the exact same quality analysis as monitoring agents provide
2. **DRY** — Single source of truth for analysis instructions (no duplication)
3. **Maintainability** — Updates to monitoring agent instructions automatically apply to Quick Analysis
4. **User Experience** — First message is immediately valuable (actionable analysis, not "How can I help you?")

#### Trade-offs
- Slightly longer wait for first message (full LLM analysis vs instant greeting)
- Users must select option type upfront (can't analyze both call and put in same session)

#### Alternatives Considered
1. **Separate instructions for chat** — Rejected: would create divergence and maintenance burden
2. **No automatic analysis** — Rejected: user explicitly requested this to match agent behavior
3. **Analyze both call and put automatically** — Rejected: would be slow and confusing to display

#### Pattern for Future Work
When building chat/analysis features that should behave like existing agents:
1. Identify the agent's instruction file
2. Import and reuse at runtime (don't duplicate)
3. Use a flag (like `first_analysis`) to switch system prompts
4. Keep the chat flow simple: automatic first message → normal Q&A

#### Files Changed
- `web/templates/chat.html` — Added dropdown, automatic first analysis trigger
- `web/app.py` — Updated endpoints to accept `option_type`, handle `first_analysis` flag, import centralized instructions

#### Related Decisions
- Chat UI Design System Alignment (2026-03-31) — established form design patterns
- Quick Analysis Button Enable Pattern (2026-03-31) — form validation pattern reused for three-input form
