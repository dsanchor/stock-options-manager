# Rusty — History

## Project Context
- **Project:** options-agent
- **User:** dsanchor
- **Stack:** Python, Microsoft Agent Framework, Azure Foundry (gpt-5.1)
- **MCP:** iflow-mcp_ferdousbhai_investor-agent 1.6.3
- **Description:** Two periodic trading agents for covered call and cash-secured put sell signals. Local runtime, configurable polling, stock symbols from files, decision logs, sell signal alerts.

## Learnings

### Open Position Monitor Architecture (2025-07)
- Added two new agents (OpenCallMonitor, OpenPutMonitor) that reuse the TradingView pre-fetch architecture but operate on open positions instead of symbol watchlists.
- Key difference: input is `EXCHANGE-SYMBOL,strike,expiration` (not just `EXCHANGE-SYMBOL`), and decisions are WAIT/ROLL (not SELL/WAIT).
- Added `run_position_monitor_agent()` to AgentRunner — TradingView-only path, with position context injected into the message template (strike, expiration alongside pre-fetched data).
- Roll signal detection (`_is_roll_signal`) and roll signal fields (`_ROLL_SIGNAL_FIELDS`) are separate from sell signal detection — different field sets, different decision values.
- Position file parsing handles comments, blank lines, and malformed lines gracefully with per-line warnings.
- Agent wrappers enforce TradingView-only: other providers get a warning and skip gracefully.
- Scheduler integration: monitors run after sell-side agents and skip silently when position files have no active lines.

### TradingView Pre-Fetch Architecture (2025-07)
- LLMs fundamentally cannot reliably make 3+ sequential browser tool calls — they skip pages, fabricate navigation errors, and ignore tool-calling instructions. Instruction-based fixes (reordering, innerText, browser_run_code) all failed.
- Solution: Pre-fetch ALL TradingView data deterministically in Python using `TradingViewFetcher` (src/tv_data_fetcher.py), then pass the data to the agent as text. Agent receives NO tools — only analyzes.
- `agent_runner.py` now branches: `mcp_provider == "tradingview"` → pre-fetch path (no tools), all other providers → existing MCP-tool flow unchanged.
- `TradingViewFetcher` uses the same Playwright MCP tools (browser_run_code for technicals/forecast, browser_navigate+click+snapshot for options chain) but driven from Python, not the LLM.
- TV instruction files (covered call + CSP) had Phase 1 rewritten from "gather data via browser tools" to "review pre-fetched data in your message". All browser_* references removed. Phase 2 analysis, trading logic, output format unchanged.
- Key pattern: when an LLM can't reliably drive a multi-step tool workflow, move the deterministic steps to the host language and let the LLM do what it's good at — analysis.

### TradingView Context Overflow Fix (2025-07)
- The TradingView Playwright agent loads pages as accessibility snapshots that can be **huge**: main page ~103K chars, technicals ~48K, forecast ~29K, options chain ~65K. Total ~245K chars exceeds gpt-5.1 context limits.
- Symptom: agent successfully loads main page + options chain, then "fails" on technicals/forecast — really context overflow, not navigation failure.
- Fix: Drop the main symbol page entirely (103K saved). Essential data (price, earnings, analyst targets) is available on forecast and options chain pages.
- Order matters: load smallest/most valuable pages first (technicals → forecast → options chain) so if context runs tight, critical technical data is already captured.
- CSP Investment Worthiness Gate was rewritten to use analyst consensus + earnings history from forecast page instead of P/E/EPS/revenue from the now-skipped main page.
- Actual IV% from expanded options chain replaces the beta/volatility proxy that came from the main page.

## Core Context

**2024-03 Foundation Work Summary:**
The options-agent project was bootstrapped with a complete Python implementation using Azure AI Agent Framework and MCP integration. Core architecture includes:
- Azure AI Agents SDK with agent lifecycle management (create → run → cleanup)
- MCP integration: initially HTTP-based (mistake), then corrected to stdio-based subprocess pattern using `agent-framework` package
- Configuration system: YAML with environment variable substitution (${VAR_NAME})
- Logging: dual-channel design (decision logs for all decisions, signal logs for SELL signals only)
- Scheduling: Python `schedule` library with graceful shutdown handlers (SIGINT/SIGTERM)
- Per-symbol analysis with historical context (last 20 decisions included in agent prompts)

**2024-03-26 Key Implementation Patterns:**
1. Env var substitution in YAML config with validation
2. Stdio-based MCP subprocess launch (`uvx iflow-mcp_ferdousbhai_investor-agent`)
3. Async agent execution with context managers for cleanup
4. Decision log context injection for continuity learning
5. Signal handlers for clean shutdown

**Infrastructure Files (Rusty's domain):**
- `config.py`: Config loader
- `logger.py`: Dual log management
- `agent_runner.py`: Agent execution + output parsing
- `main.py`: Scheduler entry point
- `covered_call_agent.py`, `cash_secured_put_agent.py`: Agent wrappers

**Instruction Files (Linus's domain):**
- `covered_call_instructions.py`: Covered call strategy prompts
- `cash_secured_put_instructions.py`: CSP strategy prompts
- Variants for different data providers (Massive.com, Alpha Vantage, TradingView)

---

### 2024-03-26: Built Complete Options Agent Python Project

Successfully implemented the complete Python project for periodic options trading agents using Azure AI Agents Framework and MCP integration.

**Key Technical Decisions:**
- **Azure AI Agents SDK**: Used `azure-ai-projects` and `azure-ai-agents` for agent creation and management
- **MCP Integration**: Implemented `McpTool` from Azure AI Agents SDK to connect to local MCP server at `http://localhost:8000/sse`
- **Scheduling**: Used Python `schedule` library for simple, readable periodic execution
- **Configuration**: Implemented YAML-based config with environment variable substitution (${VAR_NAME} pattern)
- **Logging Strategy**: Dual-log approach with decision logs (all decisions) and signal logs (SELL signals only)
- **Error Handling**: Wrapped each symbol analysis in try-except to prevent one failure from blocking others

**Architecture Implemented:**
```
config.yaml → Config → AgentRunner → [CoveredCallAgent, CashSecuredPutAgent]
                          ↓
                     Azure AI Client + MCP Tool
                          ↓
                     Per-symbol analysis with context from previous decisions
```

**Key Features:**
1. **Context Continuity**: Each analysis includes last 20 decision log entries so agents learn from previous decisions
2. **Clean Agent Lifecycle**: Create agent per symbol, run analysis, cleanup agent to avoid resource leaks
3. **Signal Detection**: Parse responses for SELL keywords and log to separate signal file for easy review
4. **Graceful Shutdown**: Signal handlers for Ctrl+C and SIGTERM
5. **Immediate + Scheduled**: Runs immediately on startup, then continues on schedule

**Coordination with Linus:**
- Did NOT create instruction files - Linus wrote `covered_call_instructions.py` and `cash_secured_put_instructions.py` in parallel
- My code imports from these files: `from covered_call_instructions import COVERED_CALL_INSTRUCTIONS`
- This parallel work pattern worked well - no blocking or conflicts

**Files Created:**
- `config.yaml` - Configuration with Azure endpoint, MCP settings, scheduling
- `src/config.py` - Config loader with env var substitution and validation
- `src/logger.py` - Log management utilities (read/append decision logs and signal logs)
- `src/agent_runner.py` - Core agent execution logic with Azure AI Agents SDK
- `src/covered_call_agent.py` - Covered call agent wrapper
- `src/cash_secured_put_agent.py` - Cash secured put agent wrapper
- `src/main.py` - Entry point with scheduler and graceful shutdown
- `data/covered_call_symbols.txt` - Sample symbols (AAPL, MSFT)
- `data/cash_secured_put_symbols.txt` - Sample symbols (NVDA, AMZN)
- `requirements.txt` - Python dependencies
- `README.md` - Comprehensive setup and usage documentation

**Implementation Patterns Worth Reusing:**
1. **Env Var Substitution in Config**: Recursive pattern matching for ${VAR_NAME} with validation
2. **MCP Tool Setup**: Simple pattern with `McpTool(server_label, server_url)` then pass `tools=mcp_tool.definitions` and `tool_resources=mcp_tool.resources`
3. **Run Polling Loop**: Check status every 2 seconds, handle COMPLETED/FAILED/CANCELLED/EXPIRED states
4. **Log Context Pattern**: Read last N log entries and include in agent prompt for continuity
5. **Signal Handler Pattern**: Set `self.running = False` on SIGINT/SIGTERM for clean shutdown

**Potential Issues to Watch:**
- MCP server must be running before starting the agents
- Azure authentication via `DefaultAzureCredential()` requires `az login`
- The `AZURE_AI_PROJECT_ENDPOINT` environment variable must be set
- Agent cleanup (`agents_client.agents.delete`) is important to avoid resource accumulation

### 2024-03-26: SDK Migration from azure-ai-agents to agent-framework

**CRITICAL FIX**: Discovered the initial implementation used the WRONG SDK. The correct SDK is `agent-framework` from https://github.com/microsoft/agent-framework (installed via `pip install agent-framework --pre`), NOT `azure-ai-agents`.

**Migration Completed:**
- Rewrote all agent execution code to use `agent-framework`
- Changed from HTTP-based MCP server to stdio-based subprocess launch
- Made agent execution async (using `asyncio.run()` bridge from sync scheduler)
- Updated all imports to use correct package names

**Key API Changes:**
```python
# OLD (azure-ai-agents - INCORRECT)
from azure.ai.agents import AIAgentsClient
from azure.ai.agents.models import McpTool, RunStatus

# NEW (agent-framework - CORRECT)
from agent_framework import Agent, MCPStdioTool
from agent_framework.foundry import FoundryChatClient
from azure.identity import AzureCliCredential
```

**MCP Integration Changes:**
- **OLD**: HTTP-based MCP server (`server_url: "http://localhost:8000/sse"`)
- **NEW**: Stdio-based subprocess (`command: "uvx"`, `args: ["iflow-mcp_ferdousbhai_investor-agent"]`)
- **Benefit**: No separate server startup needed - MCP server launches automatically as subprocess

**Execution Pattern Changes:**
- **OLD**: Synchronous with thread/run polling, manual agent create/delete
- **NEW**: Async with `await agent.run()`, automatic cleanup via context managers
- **Pattern**: `async with mcp_tool:` → `agent = Agent(...)` → `await agent.run(message)`

**Files Rewritten:**
1. `requirements.txt` - Replaced `azure-ai-agents` with `agent-framework[foundry]`
2. `config.yaml` - Replaced `server_label`/`server_url` with `command`/`args`/`description`
3. `src/config.py` - Updated validation and properties for new MCP config structure
4. `src/agent_runner.py` - Complete rewrite using Agent Framework async patterns
5. `src/covered_call_agent.py` - Made async (`async def`, `await runner.run_agent()`)
6. `src/cash_secured_put_agent.py` - Made async (`async def`, `await runner.run_agent()`)
7. `src/main.py` - Added `asyncio.run()` bridge to call async agent functions from sync scheduler
8. `README.md` - Updated to reflect correct SDK, stdio MCP pattern, and new setup steps

**Key Technical Learnings:**
1. `FoundryChatClient` uses `AzureCliCredential()` (not `DefaultAzureCredential`)
2. `MCPStdioTool` launches subprocess with `approval_mode="never_require"` for auto-approval
3. Must use `async with mcp_tool:` context manager for proper cleanup
4. `agent.run()` returns result object, convert to string with `str(result)`
5. Scheduler stays sync (schedule library), agents are async - bridge with `asyncio.run()`

**Why This Matters:**
- The `azure-ai-agents` SDK is NOT the official Microsoft agent framework
- `agent-framework` is the correct, actively maintained SDK from Microsoft
- Stdio MCP is cleaner than HTTP - no separate server management
- Async patterns are more efficient and align with modern Python practices

**Verification Needed:**
- Test that MCP server launches correctly via uvx subprocess
- Confirm agent responses are parsed and logged properly
- Verify async execution doesn't break scheduler loop
- Check that MCP tool context manager cleanup works correctly

### 2024-03-26: Migrated MCP Server from investor-agent to mcp_massive

**Migration Completed:**
Switched from `iflow-mcp_ferdousbhai_investor-agent` to `mcp_massive` (Massive.com) for financial market data.

**Key Changes:**
- **Command**: Changed from `uvx --from iflow-mcp-ferdousbhai-investor-agent investor-agent` to `mcp_massive`
- **Installation**: `uv tool install "mcp_massive @ git+https://github.com/massive-com/mcp_massive@v0.8.7"`
- **Environment**: Requires `MASSIVE_API_KEY` environment variable
- **MCP Tool Name**: Changed from `"investor-agent"` to `"massive"` in MCPStdioTool constructor

**Files Updated:**
1. `config.yaml` - Updated MCP command, args, and description
2. `src/agent_runner.py` - Changed MCP tool name from "investor-agent" to "massive"
3. `.squad/team.md` - Updated MCP Data reference to mcp_massive 0.8.7
4. `.squad/agents/rusty/charter.md` - Updated MCP Data Source
5. `.squad/agents/linus/charter.md` - Updated MCP Data Source
6. `README.md` - Updated MCP server references and setup instructions

**New MCP Server Capabilities:**
- Tools: `search_endpoints`, `get_endpoint_docs`, `call_api`, `query_data`
- Built-in functions: Black-Scholes Greeks (bs_price, bs_delta, bs_gamma, bs_theta, bs_vega, bs_rho)
- Returns calculations: simple_return, log_return, cumulative_return, sharpe_ratio, sortino_ratio
- Technical indicators: sma, ema

**Transport:** Stdio (unchanged from previous implementation)

**Why This Matters:**
- mcp_massive provides direct access to Massive.com's comprehensive financial API
- Built-in Black-Scholes and Greeks calculations reduce need for custom math
- SQL querying capability for more flexible data analysis
- Single API source reduces integration complexity

### 2026-03-26: Completed MCP Migration to mcp_massive + SDK Migration to agent-framework

**Orchestration Summary (2026-03-26T16:05):**
All migration tasks completed successfully. Rusty handled SDK migration (agent-framework) and MCP server integration, Linus updated agent instructions for new MCP composable tool architecture.

**MCP Server Integration Complete:**
- Installation: `uv tool install "mcp_massive @ git+https://github.com/massive-com/mcp_massive@v0.8.7"`
- Config structure updated for stdio-based subprocess launch
- MCPStdioTool name: "massive"
- All configuration files aligned across codebase
- Documentation updated

**SDK Migration Complete (azure-ai-agents → agent-framework):**
- Dependencies: Replaced with `agent-framework[foundry] --pre`
- Authentication: Updated to `AzureCliCredential()` (from `DefaultAzureCredential`)
- MCP integration: Stdio subprocess (from HTTP-based)
- Execution: Async/await patterns with `asyncio.run()` bridge from scheduler
- Architecture: Per-symbol agent creation/deletion pattern maintained
- All patterns (dual-log, context continuity, config substitution) preserved

**Files Consolidated:**
- Orchestration logs created for both agents (2026-03-26T16-05-rusty.md, 2026-03-26T16-05-linus.md)
- Session log created (2026-03-26T16-05-mcp-massive-migration.md)
- Decision inbox merged into decisions.md, 3 inbox files deleted
- Decisions documented with full rationale and trade-offs

**Ready for Testing:**
- Infrastructure: Rusty's implementation ready for MCP server launch verification
- Instructions: Linus's discovery-first workflow ready for agent testing
- Next steps: Basher (integration testing), Danny (end-to-end validation)

### 2026-07-25: Added Multi-Provider MCP Support (massive + alphavantage)

Implemented configurable MCP provider switching so users can choose between `massive` and `alphavantage` via `config.yaml`.

**Key Changes:**
- **config.yaml**: Restructured `mcp` section with `provider` selector and per-provider sub-sections (`massive`, `alphavantage`), each with `command`, `args`, `description`, `env_key`
- **src/config.py**: Added `mcp_provider`, `mcp_env_key` properties and `_mcp_provider_config` helper. Added `_prune_inactive_providers()` to strip inactive provider sections before env var substitution (prevents crash when inactive provider's env var isn't set). Updated `_validate()` to check provider sub-section exists with required fields. Clear error message if old config format is detected.
- **src/agent_runner.py**: Made `name` and env key check dynamic via new `mcp_provider` and `mcp_env_key` constructor params (no more hardcoded "massive" / "MASSIVE_API_KEY")
- **src/covered_call_agent.py + cash_secured_put_agent.py**: Added provider-based instruction selection — lazy-imports AlphaVantage instructions only when `alphavantage` provider is selected (so missing AV instruction files don't break massive mode)
- **src/main.py**: Passes `mcp_provider` and `mcp_env_key` through to AgentRunner constructor

**Design Decision — Lazy Import for AV Instructions:**
Used conditional lazy `from .av_*_instructions import ...` inside agent files so that the AlphaVantage instruction modules (being created by Linus) don't need to exist for massive provider to work. This avoids a hard dependency on files that don't exist yet.

**Design Decision — Prune Before Substitute:**
The `_prune_inactive_providers()` method removes inactive provider config sections before `_substitute_env_vars()` runs. This prevents `${ALPHAVANTAGE_API_KEY}` from crashing config load when the user only has `MASSIVE_API_KEY` set (and vice versa).

### 2026-07-25: Migrated Alpha Vantage to Remote MCP (Streamable HTTP)

Replaced the local stdio-based Alpha Vantage MCP server (`uvx marketdata-mcp-server`) with the remote hosted server at `mcp.alphavantage.co` using streamable HTTP transport.

**Key Changes:**
- **config.yaml**: AV section now uses `transport: "streamable_http"` and `url` instead of `command`/`args`. No local install needed.
- **src/config.py**: Added `mcp_transport` and `mcp_url` properties. Validation now branches: stdio providers require `command`+`args`, HTTP providers require `url`. `mcp_command`/`mcp_args` use `.get()` with defaults for safety.
- **src/agent_runner.py**: Imports `MCPStreamableHTTPTool` alongside `MCPStdioTool`. Creates the right tool class based on `self.mcp_transport`. API key env check runs for both transport types.
- **src/main.py**: Passes `mcp_transport` and `mcp_url` to AgentRunner constructor.
- **README.md**: Updated AV setup to note it's a remote server (no local install). Updated config example, troubleshooting, and technical details.

**Backward Compatibility:**
- Massive.com config unchanged — `transport` defaults to `"stdio"` when absent.
- All existing properties (`mcp_command`, `mcp_args`) still work for stdio providers.

**Coordination with Linus:**
Linus created Alpha Vantage instruction files in parallel. The lazy import pattern allows both provider modes to work independently. When user selects alphavantage in config, agents load AV instructions; when selecting massive, agents load Massive instructions.

**Pattern: Transport Abstraction at Config Level**

This implementation establishes a reusable pattern for supporting multiple MCP transport types:

1. **Config Schema:** Separate transport sub-sections with transport-specific fields:
   ```yaml
   mcp:
     provider: "alphavantage"
     alphavantage:
       transport: "streamable_http"
       url: "https://mcp.alphavantage.co/mcp?apikey=${ALPHAVANTAGE_API_KEY}"
     massive:
       transport: "stdio"
       command: "mcp_massive"
       args: []
   ```

2. **Validation:** `_validate()` checks required fields per transport type (not mixed validation):
   - `stdio`: `command` + `args` mandatory
   - `streamable_http`: `url` mandatory

3. **Dispatch:** `agent_runner.py` instantiates correct tool class:
   ```python
   if self.mcp_transport == "streamable_http":
       tool = MCPStreamableHTTPTool(self.mcp_provider, url=self.mcp_url, ...)
   else:
       tool = MCPStdioTool(self.mcp_provider, command=self.mcp_command, args=self.mcp_args, ...)
   ```

4. **Extensibility:** Adding a new transport (e.g., WebSocket) requires:
   - New `MCPWebSocketTool` in agent_framework
   - New transport sub-section in config with `url` field
   - One new `elif` branch in agent_runner dispatch
   - No agent changes needed (transport is abstracted)

This pattern ensures single codebase can serve multiple transport backends without coupling agent logic to transport details. Future providers (Polygon, IEX, etc.) can use existing stdio or HTTP infra without architectural changes.

### 2025-07-25: Added TradingView as 4th MCP Provider + EXCHANGE-SYMBOL Format

**Changes Made:**
- **config.yaml**: Added `tradingview` provider using `mcp-server-fetch` (uvx). No API key needed — it's a generic fetch server. Updated provider comment to list all four options.
- **covered_call_agent.py / cash_secured_put_agent.py**: Added `elif provider == "tradingview"` branches with lazy imports for `TV_COVERED_CALL_INSTRUCTIONS` and `TV_CASH_SECURED_PUT_INSTRUCTIONS` (instruction files to be created by Linus).
- **agent_runner.py**: Updated `run_agent()` message template to parse `EXCHANGE-SYMBOL` format (e.g., "NYSE-AA" → exchange="NYSE", ticker="AA"). Updated `_extract_decision_line()` to match on ticker only (not the full exchange-symbol string).
- **Symbol files**: Changed `covered_call_symbols.txt` and `cash_secured_put_symbols.txt` to use `EXCHANGE-SYMBOL` format with header comment.

**Key Design Decisions:**
- The exchange-symbol parsing uses `split('-', 1)` to handle edge cases (symbols with hyphens after the exchange prefix).
- Backward-compatible: if a symbol has no dash, exchange defaults to empty string and ticker is the full symbol.
- `_extract_decision_line` now uses ticker for matching so decision logs show clean ticker names, not "NASDAQ-AAPL".


**Status:** ✅ Completed 2026-03-26T22:40:00Z  
**Team:** Coordination with Linus (instruction files), Coordinator (README), Danny (feature request)

### 2025-07-25: Redesigned Output Format — JSON + SUMMARY

Replaced the pipe-delimited output format across ALL 8 instruction files, plus updated agent_runner.py and logger.py to support structured JSON decision output.

**Design:**
- Agent now outputs a fenced ```json block with a full decision schema, followed by a `SUMMARY:` line
- JSON is logged to `.jsonl` companion files (one JSON object per line) for downstream machine consumption
- SUMMARY line logged to existing `.log` files for human readability
- Backward-compatible: agent_runner.py tries JSON extraction first, falls back to legacy pipe format

**JSON Schema Fields:**
- Common: timestamp, symbol, exchange, agent, decision, strike, expiration, dte, iv, iv_rank, delta, premium, premium_pct, underlying_price, reason, waiting_for, confidence, risk_flags
- CSP-only: `support_level` (nearest significant support price)
- `agent` field: `"covered_call"` or `"cash_secured_put"` — only difference between CC and CSP schemas

**Key Implementation Patterns:**
1. `_try_extract_json()`: Fenced code block regex first, then raw JSON object scanning
2. `_extract_summary_line()`: Simple `SUMMARY:` prefix match
3. `_extract_decision_line()` now returns `Tuple[str, Optional[Dict]]` — (summary, json_data)
4. `_is_sell_signal()` accepts optional json_data for structured detection
5. `append_decision_json()` in logger.py writes to `.jsonl` derived from `.log` path
6. `_jsonl_path()` helper: `os.path.splitext` + `.jsonl` extension swap

**Files Changed (13 total):**
- 8 instruction files (all *_instructions.py) — OUTPUT FORMAT, INTERPRETING PREVIOUS DECISION LOG, CLEAR SELL SIGNAL, RESPONSE STRUCTURE sections
- `src/agent_runner.py` — JSON extraction, summary extraction, dual logging, updated _is_sell_signal
- `src/logger.py` — added append_decision_json(), _jsonl_path()
- `config.yaml` — comment update (gpt-5.1 first in options)
- `.squad/team.md` — updated model reference to gpt-5.1
- `.squad/agents/rusty/history.md` — updated project context + this entry

**Model Update:** gpt-5.4-mini → gpt-5.1 in team.md and history.md project context.

### 2025-07: Switched Technicals/Forecast to browser_run_code (Context Reduction)

**Problem:** TradingView agent was reporting "technicals/forecast pages failed to load" even though navigation worked fine. Root cause: `browser_navigate` returns full accessibility snapshots (~48K + ~38K = ~86K chars for just those two pages), overwhelming the model's context window.

**Solution:** Replaced `browser_navigate` with `browser_run_code` for technicals and forecast pages. `browser_run_code` takes a Playwright JS function that navigates to the URL AND extracts `innerText`, returning only ~3K chars (technicals) and ~2.4K chars (forecast) — a 15-16x reduction. The `innerText` contains ALL the same data (oscillators, MAs, pivots, earnings, analyst consensus) in clean tab-separated format.

**Key Details:**
- `browser_run_code(code='async (page) => { await page.goto(URL, { waitUntil: "networkidle" }); await page.waitForTimeout(2000); return await page.evaluate(() => { const main = document.querySelector("main") || document.body; return main.innerText; }); }')`
- Options chain KEPT as `browser_navigate` + `browser_click` + `browser_snapshot` — it needs accessibility tree element refs for clicking to expand expiration rows
- Updated Context Budget note to explain the two-tool strategy
- Updated tool listings to include `browser_run_code` in both instruction files
- Both files: `tv_covered_call_instructions.py`, `tv_cash_secured_put_instructions.py`

**Context savings per analysis run:** ~80K chars freed (from ~188K total down to ~108K), leaving much more room for the options chain data and the model's analysis.


### README Documentation Enrichment (2025-07)
- Added "How It Works" section with end-to-end flow diagram showing scheduler → agent → per-symbol loop → log.
- Added "Key Concepts" section covering Decision vs Signal semantics, TradingView pre-fetch architecture rationale, per-symbol context filtering, and JSONL output format.
- Enhanced Configuration section with full annotated `config.yaml` including the `context:` section.
- Enhanced Output section with example decision JSON object from JSONL log.
- Enhanced Project Structure with `tv_data_fetcher.py` and 1-line descriptions for every file.
- Key approach: read all source files first to document actual behavior, not assumed behavior.

### Profit Optimization Signals (2025-07)
- Added profit optimization logic to both open position monitor instruction files: ROLL_DOWN for calls (lower strike → more premium when bearish) and ROLL_UP for puts (higher strike → more premium when bullish).
- Key design: 9-condition unanimous consensus gate. ALL conditions must pass — deep OTM, very low delta, technicals aligned, MAs aligned, no catalysts, analyst sentiment not contrary, low IV, DTE > 14, stable decision history. If even one is ambiguous → WAIT.
- `agent_runner.py` required zero changes — `_ROLL_DECISIONS` already includes ROLL_DOWN/ROLL_UP, and `risk_flags` is already in `_ROLL_SIGNAL_FIELDS`. The `"profit_optimization"` flag is the semantic marker distinguishing profit rolls from defensive rolls.
- No JSON schema changes needed — only instruction prompt enrichment. This is the correct pattern: schema stays stable, agent behavior evolves through instructions.
- Added output examples in both instruction files showing the profit optimization JSON shape with the `profit_optimization` risk flag.

### TradingView Overview Page Addition (2025-07)
- Added `fetch_overview()` as 4th pre-fetched resource in `TradingViewFetcher`. Follows same `browser_run_code` + innerText pattern as technicals/forecast.
- Overview page (`/symbols/{EXCHANGE}:{TICKER}/`) provides fundamentals: price, market cap, P/E, dividend yield, 52-week range, volume, sector/industry. Previously skipped to save context — now re-added as first fetch.
- CSP instructions updated: Investment Worthiness Assessment no longer needs the "main page not loaded" workaround — overview data now available directly.
- Instruction files renumbered: overview=1, technicals=2, forecast=3, options chain=4 in both covered call and CSP.

### Web Dashboard (2025-07)
- Built a full web UI using FastAPI + Jinja2 + vanilla JS (no build step). Dark trading theme with card-based layout.
- Architecture: `web/app.py` has all routes + JSONL utility functions. Templates in `web/templates/`, static assets in `web/static/`. Entry point `run_web.py` at project root.
- Dashboard reads JSONL log files directly — no database. Parses timestamps, groups by symbol, counts by time range (today/week/month/total).
- Signal detail pages show full signal JSON + backing decisions found by matching symbol + timestamp within a 2-hour window.
- Settings page edits data files with a form POST; changes are picked up on the next scheduler tick since `_read_symbols()` and `_read_positions()` already read from disk on every call (verified — no caching).
- Chat feature uses Azure OpenAI directly (not agent framework) with the last 20 decisions from each log file as context.
- For the AzureOpenAI client, the `/api` suffix on the project endpoint must be stripped.
- Config is loaded via raw YAML read (not `src.config.Config`) to avoid requiring MCP env vars that the web app doesn't need.
- Added auto-refresh toggle (60s interval, persisted in localStorage), clickable table rows, responsive CSS for mobile.
- Dependencies: fastapi, uvicorn[standard], jinja2, python-multipart, openai.

### Consolidated Entry Point (2025-07)
- Created `run.py` as single entry point: `python run.py` starts both web dashboard (uvicorn) and scheduler (daemon thread).
- Uses FastAPI lifespan context manager to start/stop the scheduler thread alongside the web server.
- `OptionsAgentScheduler.run()` now accepts `install_signals=True` param — set to `False` when running in a thread since signal handlers can only be installed from the main thread.
- CLI flags via argparse: `--web-only`, `--scheduler-only`, `--port PORT`. `--web-only` and `--scheduler-only` are mutually exclusive.
- `run_web.py` kept for backwards compat but delegates to `run.py --web-only` via `runpy.run_path`.
- Web host/port read from `config.yaml` `web:` section, overridable with `--port`.
- Lifespan is attached via `app.router.lifespan_context = lifespan` to avoid modifying `web/app.py`'s app creation.

### TradingView-Only Simplification (2025-07)
- Removed all non-TradingView MCP providers (Massive.com, Alpha Vantage, Yahoo Finance) — 6 instruction files deleted, ~4100 lines removed.
- `config.yaml` MCP section flattened: no more `provider` key or per-provider sub-sections. Just `command`, `args`, `description` at top level.
- `config.py` simplified: removed `_prune_inactive_providers()`, `mcp_provider`, `_mcp_provider_config`, `mcp_transport`, `mcp_url`, `mcp_env_key` properties. Validation just checks `mcp.command` exists.
- `agent_runner.py` simplified: removed entire non-TradingView else branch (MCP tool creation, HTTP transport, API key validation, tool discovery). TradingView pre-fetch is now the only code path.
- Agent wrappers (`covered_call_agent.py`, `cash_secured_put_agent.py`) always use TV instructions directly — no provider branching.
- Position monitors (`open_call_monitor_agent.py`, `open_put_monitor_agent.py`) removed TradingView-only guard since there's no other provider.
- `main.py` setup passes only 5 params to AgentRunner (removed provider, env_key, transport, url).
- README updated: removed provider comparison table, multi-provider setup/troubleshooting, env var docs for MASSIVE/ALPHAVANTAGE.
- `web/app.py` had no provider references — no changes needed.

### Signal Log vs Decision Log Separation (2025-07)
- Dashboard counts, signals list, and signal detail routes now ALL read from `signal_log` exclusively — never `decision_log`. Previously, position monitors incorrectly read from `decision_log`, counting WAIT decisions as signals.
- `decision_log` contains every analysis outcome (WAIT, SELL, ROLL, CLOSE). `signal_log` contains only actionable signals (SELL, ROLL, CLOSE — never WAIT). Dashboard/signals pages should always use `signal_log` for accurate counts.
- Signals list page (`/signals/{agent_type}/{symbol}`) now also loads the last 20 decisions from `decision_log` as a "Recent Decisions" context section below the signals table.
- Settings page `DATA_FILES` dict reordered: open positions first (calls, puts), then following/watchlist symbols — matches dashboard visual order.
- Key file paths: `web/app.py` (FastAPI app), `web/templates/signals.html` (signal list + decisions template), `web/templates/settings.html` (settings template).

### Dashboard Signal Fixes (2026-03-27)
- Fixed dashboard signal counts: changed from reading `decision_log` (inflated by WAIT decisions) to `signal_log` (actionable signals only). Eliminated false count inflation.
- Added "Recent Decisions" section to signals list page — provides context for signal analysis using `decision_log` entries.
- Reordered settings page fields to match dashboard layout for better UX consistency.
- All three fixes committed as 3a3435e.

### Consolidated Entry Point (2025-07)
- Created `run.py` as unified entry point for both web dashboard and scheduler.
- Scheduler runs as daemon thread managed by FastAPI lifespan context — avoids signal handler conflicts.
- CLI flags (`--web-only`, `--scheduler-only`) provide fine-grained control. `run_web.py` kept as backwards-compat shim.
- Host/port read from `config.yaml` `web:` section; `--port` flag overrides.

### TradingView Pre-Fetch Refinement (2025-07-28)
- Added web dashboard as separate entry point using FastAPI + Jinja2 templates. Dark trading theme, no build step.
- Architecture: dashboard and scheduler run independently, both read same JSONL logs. No database layer.
- Config loading simplified for web app — reads `config.yaml` raw YAML (no MCP env vars needed).
- Chat endpoint uses direct Azure OpenAI API with context from last 20 decisions per log.
- Position files read from disk on every request — hot-reload confirmed, edits take effect on next scheduler tick.


### Dashboard Quick Wins (2025-07)
- Added `_latest_decisions_by_key()` helper in `web/app.py` — reads decision_log once and returns a dict keyed by symbol/position key with the most recent decision entry. Used by `_build_agent_table()` to attach health metrics without changing signal count logic.
- Position monitor dashboard rows now show DTE, moneyness (colored badge), assignment risk (colored badge), and delta from the latest decision entry.
- All dashboard rows (position monitors + covered call/CSP) now show `risk_flags` from the latest decision as `.flag` pill badges in a Flags column.
- Signals list page (signals.html) for covered call/CSP now shows IV, Premium ($X.XX), and Delta columns. Position monitor signals unchanged.
- CSS additions: `.badge-moneyness-{itm,otm,atm}`, `.badge-risk-critical` (separated from high with brighter red), `.metric-value` (mono font for numeric cells).
- Key pattern: dashboard data enrichment reads decision_log separately from signal_log — signal counts come from signal_log (actionable only), health metrics from decision_log (latest analysis).
- Jinja2 `format` filter used for numeric formatting: `"%.2f" | format(val)` with `is not none` guards.

### Cron Settings, Trigger Buttons & No Auto-Run (2025-07)
- Cron expression now editable from Settings page. Saved to `config.yaml` via `_write_config()` (yaml.dump). Live reschedule via `scheduler.reschedule(new_cron)` — sets a `_cron_changed` flag that the run loop checks before each sleep.
- Scheduler instance exposed to web via `app.state.scheduler` (set in `run.py` lifespan). Web code uses `getattr(request.app.state, "scheduler", None)` for safe access in web-only mode.
- Dashboard "Run Now" buttons added per agent card. POST `/api/trigger/{agent_type}` launches agent in a daemon thread via `threading.Thread(daemon=True)`. Reuses scheduler's config/runner — no duplicate init.
- Removed auto-run on startup (`run_all_agents()` call in `run()` method). Scheduler now only fires on cron schedule.
- JS trigger handler: fetch POST → visual feedback cycle (Running → Triggered/Error → reset after 3s). Button state managed via CSS classes `.running`, `.done`, `.error`.
- Key pattern: for web↔scheduler communication, `app.state` is the simplest bridge — no module-level globals or import cycles needed.

### Timestamp Consistency Fix (2025-07)
- All decision and signal log timestamps now generated in Python (`datetime.now().strftime("%Y-%m-%d %H:%M:%S")`) BEFORE agent execution — LLM-generated timestamps are overridden.
- Single `TIMESTAMP_FORMAT` constant in `agent_runner.py` used across all 6 logging paths (structured decisions, fallback decisions, error decisions, sell signals, roll signals — for both `run_agent` and `run_position_monitor_agent`).
- `_build_signal_data()` and `_build_roll_signal_data()` now accept a `timestamp` parameter instead of generating their own.
- LLM instruction schemas updated to `"timestamp": "auto-set by system"` — field kept in schema so LLM output remains parseable, but value is always replaced.
- `web/app.py` `parse_timestamp()` updated to recognize `%Y-%m-%d %H:%M:%S` as the primary format (checked first, before ISO variants).
- Key pattern: when you need consistent metadata across LLM outputs, always inject it from the calling code — never trust the LLM to generate accurate timestamps, IDs, or other metadata.

### API Key Authentication Switch (2025-07)
- Replaced `AzureCliCredential` with API key authentication for Azure OpenAI.
- Removed `azure-identity` dependency from `requirements.txt` — simpler setup, better Docker compatibility.
- `AzureOpenAIChatClient` now uses `api_key` parameter instead of `credential`.
- Config changes: added `api_key: "${AZURE_OPENAI_API_KEY}"` to `config.yaml` azure section, with validation in `Config._validate()`.
- `AgentRunner.__init__()` now accepts `api_key` parameter, passed from `Config.api_key` property.
- README.md: replaced `az login` prerequisite with `AZURE_OPENAI_API_KEY` env var setup.
- Dockerfile: removed `.azure` mount from Docker run commands, added `AZURE_OPENAI_API_KEY` env var to examples.
- Troubleshooting: replaced "run az login" with "check your API key" guidance.
- Key files: `src/agent_runner.py` (client init), `src/config.py` (api_key property + validation), `src/main.py` (runner instantiation), `config.yaml`, `requirements.txt`, `README.md`.
- Key pattern: API key auth is simpler for containerized workloads — no need to mount Azure CLI state or manage token refresh.
