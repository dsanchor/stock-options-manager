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

## Governance

- All meaningful changes require team consensus
- Document architectural decisions here
- Keep history focused on work, decisions focused on direction
