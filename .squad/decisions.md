# Squad Decisions

## Active Decisions

### 1. Trading Agent Instructions Design
**Date:** 2024-01-15  
**Author:** Linus (Quant Dev)  
**Status:** Completed  
**Impact:** Team-wide (defines agent behavior)

#### Context
Created system prompt instructions for covered call and cash-secured put agents. These instructions define how Azure AI Agents will analyze market data and make trading decisions.

#### Key Design Decisions

1. **Dual-Threshold Decision Framework**
   - **Standard SELL criteria**: Solid setups with IV Rank ≥50, proper Greeks, clean calendar
   - **CLEAR SELL SIGNAL criteria**: Exceptional setups (premium 2-2.5%, IV Rank ≥70) that trigger alerts
   - **Rationale**: Separates "good" opportunities from "don't miss this" opportunities

2. **Greeks-Based Strike Selection**
   - **Covered Calls:** Conservative (Δ 0.20-0.25), Moderate (Δ 0.25-0.30), Aggressive (Δ 0.30-0.35)
   - **Cash-Secured Puts:** Strike AT or BELOW support levels with same delta ranges
   - **Rationale**: Assignment on puts should happen at attractive prices (support), not above

3. **Standardized Output Format**
   - `[TIMESTAMP] SYMBOL | DECISION: SELL/WAIT | Strike: $X | Exp: YYYY-MM-DD | IV: X% | Reason: ... | Waiting for: ...`
   - **Rationale**: Enables easy parsing for decision logs and downstream analysis

4. **Fundamental Quality Gate (CSP Only)**
   - Mandatory check: "Would you want to own this stock at strike price?"
   - If NO → automatic WAIT regardless of premium
   - **Rationale**: Bad assignment on deteriorating stock wipes out months of premium

5. **Optimal DTE Window: 30-45 Days**
   - Balances premium amount with theta decay rate
   - Avoids <21 DTE (insufficient premium) and >60 DTE (too much time risk)
   - **Rationale**: Theta acceleration in final 30 days, but need enough time to manage position

6. **Earnings Calendar Integration**
   - **Covered Calls:** NEVER sell expiring after next earnings (gap risk)
   - **Cash-Secured Puts:** IDEAL to sell 1-3 days post-earnings (capture IV crush)
   - **Rationale**: Different risk profiles—calls fear upward gaps, puts benefit from volatility collapse

7. **MCP Tool Integration Strategy**
   - Phase 1: Core data (ticker, price history, options chain)
   - Phase 2: Volatility/sentiment (earnings calendar, fear/greed, trends)
   - Phase 3: Institutional context (holders, insiders)
   - **Rationale**: Systematic data gathering ensures no analysis gaps

#### Implications
- Instructions are Python string constants for Azure AI Agent's `instructions` parameter
- Decision logs must be appended to instruction context on each run
- CLEAR SELL SIGNAL marker enables alert detection in frontend
- Test edge cases: low IV, pre-earnings, post-earnings

#### Trade-offs
1. **Complexity vs. Flexibility**: Comprehensive (~12-18KB) to reduce hallucination
2. **Strict Rules vs. Agent Discretion**: Rules-based with interpretation room in "Reason" field
3. **Strike Selection**: Fixed delta ranges (0.20-0.35) per industry standard

---

### 2. Python Implementation Architecture (agent-framework SDK)
**Date:** 2024-03-26  
**Author:** Rusty (Python Dev)  
**Status:** In Progress (SDK migration from azure-ai-agents)  
**Impact:** Technical (defines project structure and integration points)

#### Context
Building complete Python project for periodic options trading agents with Azure AI Agents Framework and MCP integration.

#### Key Design Decisions

1. **Agent Framework SDK for Agent Management**
   - **Decision**: Use `agent-framework` SDK (correct) instead of `azure-ai-agents` (incorrect)
   - **Rationale**: Official framework for Microsoft Foundry with proper abstractions
   - **Impact**: Clean, maintainable code with proper resource cleanup

2. **Per-Symbol Agent Creation**
   - **Decision**: Create new agent for each symbol analysis, then delete after completion
   - **Rationale**: Avoids thread state accumulation, cleaner isolation, prevents cross-contamination
   - **Trade-off**: Slightly higher latency per symbol, worth it for reliability

3. **Dual-Log Strategy**
   - **Decision**: Maintain decision log (all decisions) and signal log (SELL only)
   - **Rationale**: Decision log captures history for context; signal log enables quick trader review
   - **Impact**: Better UX—traders know exactly where to look for actionable signals

4. **Context Continuity via Log Reading**
   - **Decision**: Read last 20 decision log entries and include in each analysis prompt
   - **Rationale**: Agents learn from previous decisions, avoid flip-flopping, provide temporal context
   - **Implementation**: `read_decision_log()` called before each analysis run

5. **Simple Scheduling with Python `schedule` Library**
   - **Decision**: Use `schedule` library instead of cron or APScheduler
   - **Rationale**: Simple readable syntax, no external dependencies, easy to test/debug
   - **Trade-off**: Less robust than systemd timers, sufficient for this use case

6. **Environment Variable Substitution in Config**
   - **Decision**: Support `${VAR_NAME}` syntax in config.yaml with validation
   - **Rationale**: Azure endpoints are sensitive and environment-specific
   - **Pattern**: Recursive substitution handles nested configs

7. **Separate Instruction Files**
   - **Decision**: Import instructions from separate files rather than inline strings
   - **Rationale**: Enables parallel development, cleaner separation of concerns
   - **Coordination**: Import pattern: `from covered_call_instructions import COVERED_CALL_INSTRUCTIONS`

#### Architecture
```
config.yaml → Config → AgentRunner → [CoveredCallAgent, CashSecuredPutAgent]
                          ↓
                  Agent Framework + MCP Tool
                          ↓
                Per-symbol analysis with decision context
```

#### Trade-offs
1. **Agent Reuse vs. Fresh Creation**: Chose fresh for isolation; can revisit if latency becomes issue
2. **Inline vs. File-Based Configs**: Chose files for easier management across environments
3. **Polling vs. Event-Driven**: Chose scheduled polling for simplicity and consistency

#### Current Issue
Used `azure-ai-agents` SDK in original implementation. Needs migration to `agent-framework` SDK. Architecture and patterns remain valid.

---

### 3. Switch MCP Server to mcp_massive
**Date:** 2026-03-26  
**Decider:** Rusty (Agent Dev)  
**Status:** ✅ Completed  
**Impact:** Team-wide (data source integration)

#### Context

The project was initially configured to use `iflow-mcp-ferdousbhai-investor-agent` as the MCP server for financial market data. Migration to `mcp_massive` from Massive.com (https://github.com/massive-com/mcp_massive) for improved data access and composable tool architecture.

#### Decision

Migrated all MCP server references and configuration from `iflow-mcp_ferdousbhai_investor-agent` to `mcp_massive v0.8.7`.

#### Rationale

- **Comprehensive API**: mcp_massive provides access to Massive.com's financial data API covering stocks, options, forex, and crypto
- **Built-in Analytics**: Includes Black-Scholes Greeks calculations (bs_price, bs_delta, bs_gamma, bs_theta, bs_vega, bs_rho)
- **SQL Querying**: Supports SQL-based data queries for flexible analysis
- **Technical Indicators**: Built-in SMA, EMA, and return calculations
- **Single Source**: Consolidates financial data access through one well-maintained API

#### Implementation

**Changed Files:**
1. `config.yaml` - Updated MCP command from `uvx --from iflow-mcp-ferdousbhai-investor-agent investor-agent` to `mcp_massive` with empty args
2. `src/agent_runner.py` - Changed MCPStdioTool name from `"investor-agent"` to `"massive"`
3. `.squad/team.md` - Updated MCP Data reference
4. `.squad/agents/rusty/charter.md` - Updated MCP Data Source in Tech Context
5. `.squad/agents/linus/charter.md` - Updated MCP Data Source in Tech Context
6. `README.md` - Updated all MCP server references and setup instructions

**Installation Requirements:**
```bash
uv tool install "mcp_massive @ git+https://github.com/massive-com/mcp_massive@v0.8.7"
export MASSIVE_API_KEY="your-api-key"
```

**Transport:** Stdio (unchanged) - MCP server launches as subprocess, no HTTP server needed.

#### Consequences

**Positive:**
- Access to comprehensive Massive.com financial data API
- Built-in Black-Scholes Greeks simplify options calculations
- SQL querying enables more flexible data analysis
- Reduced complexity with single API source

**Neutral:**
- Requires `MASSIVE_API_KEY` environment variable (similar to previous setup requirements)
- Installation via `uv tool install` (slightly different from uvx pattern)

**Mitigations:**
- Linus updated agent instructions to ensure compatibility with mcp_massive tools
- Fallback strategies documented for missing data signals

#### Next Steps

1. **Basher**: Test that MCP server launches correctly with `mcp_massive` command
2. **Danny**: Run end-to-end test to confirm agents can successfully fetch data and generate signals
3. **Team**: Verify that `MASSIVE_API_KEY` is documented and available in deployment environment

---

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
