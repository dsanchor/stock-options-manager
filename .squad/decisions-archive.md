# Squad Decisions — Archive

Archived decisions older than 30 days (archived: 2026-04-01).

## Archived Decisions

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
   - **Decision**: Substitute `${ENV_VAR}` in config at startup, fail fast if missing
   - **Rationale**: Secrets stay out of repo, cleaner separation of config/secrets
   - **Implementation**: Use `string.Template.substitute()`

#### Implications

- Instruction files are stored as Python string constants in `src/` (easy to maintain and version-control)
- Agent creation is ephemeral—agents are created per-run then immediately deleted
- Signal logs are separate from decision logs, enabling different retention/visibility rules
- Scheduling loop is the "heartbeat" of the system; failures here halt all analysis

#### Trade-offs

1. **Complexity vs. Simplicity**: Scheduling library is simpler but less robust than cron
2. **Ephemeral Agents vs. Reusable**: Slightly higher latency for cleaner isolation
3. **String Constants vs. Jinja**: Python strings are simpler to version-control and test

---

### 3. Switch MCP Server to mcp_massive
**Date:** 2026-03-26  
**Author:** Linus (Quant Dev)  
**Status:** Completed  
**Impact:** Technical (data integration)

#### Context

Initial MCP server was built on custom endpoints. Team decided to evaluate Massive.com's MCP server (`mcp_massive`) for cleaner data access and built-in tools (earnings, technicals, Greeks, sentiment).

#### Decision

Migrate MCP server to `mcp_massive` (Massive.com's official MCP implementation).

#### Rationale

1. **Built-in Financial Tools**: Black-Scholes Greeks, technical indicators (RSI, BBANDS, MACD), earnings data, sentiment scoring
2. **SQL Querying**: Structured data access via SQL `SELECT` statements instead of REST endpoints
3. **Single API Source**: Consolidates multiple data providers (price history, options chain, fundamentals, news)
4. **No Custom Maintenance**: Rely on Massive.com team for data pipeline updates
5. **Industry Standard**: More maintainable than custom implementation

#### Implications

- `mcp_massive` command manages the MCP server lifecycle (auto-start/restart)
- Agents query via SQL (more powerful than REST) for complex analysis
- Installation: `uv tool install massive` (user's local setup)
- `MASSIVE_API_KEY` required in environment

#### Trade-offs

**Advantages:**
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

