# Rusty — History

## Project Context
- **Project:** options-agent
- **User:** dsanchor
- **Stack:** Python, Microsoft Agent Framework, Azure Foundry (gpt-5.1)
- **MCP:** iflow-mcp_ferdousbhai_investor-agent 1.6.3
- **Description:** Two periodic trading agents for covered call and cash-secured put sell signals. Local runtime, configurable polling, stock symbols from files, decision logs, sell signal alerts.

## Learnings

### CosmosDB Settings Container (2026-03-30)
- Added new `settings` container to CosmosDB with partition key `/setting_key` for persistent runtime configuration.
- Implemented deep-merge logic in `CosmosDBService.update_setting()` — partial updates preserve nested values (e.g., updating `telegram.bot_token` keeps `telegram.channel_id` intact).
- Integrated settings handler into `main.py` startup: initializes container on first run, no-op if exists.
- Updated `src/telegram_notifier.py` to read configuration from settings container on startup, with fallback to env vars.
- Added web API endpoints in `web/app.py`: `GET /api/settings/{key}` retrieves nested config, `PUT /api/settings/{key}` updates with validation.
- Settings page in dashboard now reads/writes directly to CosmosDB instead of file-based config.
- Atomic updates via CosmosDB replace_item, no race conditions on nested field updates.
- Commit: fa64388.

### Telegram Alert Notifications (2026-03-29)
- Created `src/telegram_notifier.py` with `TelegramNotifier` class — sends formatted decision and alert messages to Telegram channel.
- Config properties: `telegram_bot_token`, `telegram_channel_id`, `telegram_enabled` in config.py. Configured via `telegram.bot_token` and `telegram.channel_id` env vars in config.yaml.
- Integrated into `agent_runner.py`: notifier initialized in `PeriodicAgentRunner.__init__()`, called after each decision/alert in `run()` method.
- Message formatting uses URL-safe markdown for Telegram, graceful fallback to console logging if notifier disabled or channel not configured.
- Dependencies: `python-telegram-bot>=20.0`.
- Commit: e522f29.

### Manual Roll Endpoint (2025-07)
- Made `source` and `closing_source` optional (`None` default) in `roll_position()`, plus added `notes` param (default `""`). Signal-based rolls still pass both explicitly — no behavioral change for existing callers.
- New endpoint `POST /api/symbols/{symbol}/positions/{position_id}/roll` accepts `new_strike`, `new_expiration`, and optional `notes`. Infers position type (call/put) from the existing position doc rather than requiring the caller to specify it.
- `rolled_to`/`rolled_from` links are always set regardless of whether source snapshots are provided — traceability is maintained for manual rolls too.
- Manual rolls produce no `source`/`closing_source` fields on the position docs, which is the distinguishing signal that a roll was user-initiated vs. agent-initiated.

### Runtime Telemetry Infrastructure (2025-07)
- Added a second CosmosDB container `telemetry` (partition key `/metric_type`) alongside the existing `symbols` container. Initialization is best-effort — if the container doesn't exist, `self.telemetry_container` is set to `None` and all writes silently skip.
- `write_telemetry()` writes documents with 30-day TTL (`ttl: 2592000`). Requires the container to have `defaultTtl` set to `-1` (per-doc TTL enabled without a container default). Provisioning script updated with `--default-ttl -1`.
- `TradingViewFetcher.fetch_all()` now wraps each resource fetch with `time.time()` and stores per-resource `duration`/`size` in `self.last_fetch_stats`. The fetcher itself doesn't touch CosmosDB — the caller (AgentRunner) reads `fetcher.last_fetch_stats` after completion.
- Both `run_symbol_agent()` and `run_position_monitor()` write telemetry in a dedicated `try/except` block AFTER the main try/except — so telemetry failures never block agent execution or mask the real error.
- `get_telemetry_stats()` queries all docs in the last 30 days and aggregates in Python (CosmosDB's aggregation isn't rich enough for avg + group by on different fields). Returns nested dict for both `tv_fetch` and `agent_run` metric types.
- Settings page now shows a "Runtime Stats (Last 30 Days)" card above the scheduler card, using the same `card settings-card` styling. Template iterates over a fixed resource/agent_type order for consistent display.

### Phase 3 — Web Dashboard CosmosDB Refactor (2025-07)
- Rewrote `web/app.py` to eliminate all file-based data access (JSONL reads, txt symbol lists). All data now flows through `CosmosDBService` initialized at startup via `@app.on_event("startup")`.
- Added full REST API: symbol CRUD (`/api/symbols`), position management (`/api/symbols/{symbol}/positions`), data views (`/api/signals`, `/api/decisions`). All `/api/*` endpoints return JSON with proper HTTP status codes.
- Dashboard data still server-side rendered but sourced from CosmosDB: `list_symbols()`, `get_all_signals()`, `get_all_decisions()`. Agent tables built from the same AGENT_TYPES metadata dict (simplified to labels only — no file paths).
- New Symbols page (`/symbols`) with inline toggle switches for watchlist flags (CC/CSP) — vanilla JS calling PUT `/api/symbols/{symbol}`. Add/delete symbols with confirmation.
- New Symbol Detail page (`/symbols/{symbol}`) — positions table with add/close/delete actions, recent decisions and signals across all agent types.
- Settings page simplified: cron expression only + read-only CosmosDB diagnostics (endpoint, database, connection status). Data file editors removed.
- `_clean_doc()` strips CosmosDB system properties (`_rid`, `_self`, `_etag`, `_attachments`, `_ts`) from API responses.
- Dashboard clickable rows now link to `/symbols/{symbol}` instead of old `/signals/{agent_type}/{symbol}` routes.
- Chat endpoint updated to pull context from CosmosDB instead of JSONL files.
- Kept old signal/decision detail templates in place (harmless) but removed routes that served them.
- Key pattern: for symbol updates beyond watchlist flags (e.g., display_name), used `cosmos.container.replace_item()` directly since the service layer method was watchlist-only. This keeps the change minimal without modifying `cosmos_db.py`.

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

### CosmosDB Foundation — Phase 1 (2025-07)
- Replaced the file-based data model (`.txt` symbol lists, `.jsonl` logs) with a symbol-centric CosmosDB backend.
- New files: `src/cosmos_db.py` (CosmosDBService — 18 methods), `src/context.py` (ContextProvider adapter).
- Modified files: `src/config.py` (added cosmosdb section, removed per-agent file path properties), `config.yaml` (added cosmosdb section, removed covered_call/cash_secured_put/open_call_monitor/open_put_monitor sections), `requirements.txt` (added azure-cosmos>=4.7.0).
- **Architecture decision:** Hybrid document model — single container "symbols" with partition key `/symbol`, three doc types: `symbol_config`, `decision`, `signal`. All cross-type queries within a symbol are single-partition.
- **TTL support:** `write_decision()` accepts optional `ttl_seconds` param; `decision_ttl_days` config field (default 90) enables automatic cleanup.
- **Context injection pattern:** `ContextProvider` produces the same `reason`-per-line format that `logger.read_decision_log` / `read_signal_log` did, so agent instructions require zero changes.
- **Config validation:** `cosmosdb.endpoint` and `cosmosdb.key` are now required fields; env var substitution pattern unchanged (`${COSMOSDB_ENDPOINT}`, `${COSMOSDB_KEY}`).
- **Key files:** `src/cosmos_db.py`, `src/context.py`, `src/config.py`, `config.yaml`.
- **Dependency:** `azure-cosmos>=4.7.0` added to `requirements.txt`.

## Cross-Agent Impact

### Phase 1–3 Implementation (2026-03-28)
This 3-phase CosmosDB refactor directly impacts:
- **Danny (Lead):** Delivered all 8 sections of architecture (phases 1–4a complete as of 2026-03-28)
- **Linus (Quant):** Agent instruction files remain unchanged — context output format identical (reason-per-line, oldest-first)
- **Basher (Tester):** Phases 1–3 tested before handing to Phase 4a (provisioning)
- **Orchestration log:** See `.squad/orchestration-log/2026-03-28T1350-rusty-phase1.md`, `2026-03-28T1355-rusty-phase2.md`, `2026-03-28T1400-rusty-phase3.md`

---

### Phase 2: Scheduler + Agent Runner CosmosDB Refactor (2025-07)
- Refactored `agent_runner.py`: removed all file I/O (`_read_symbols`, `_read_positions`, file-based `append_decision/signal`). Now accepts `CosmosDBService` and `ContextProvider` as dependencies.
- Two new entry methods: `run_symbol_agent()` (for covered call + CSP) and `run_position_monitor()` (for open call/put monitors). Each handles a single symbol/position — scheduler owns the iteration loop.
- Context injection uses `ContextProvider.get_context(symbol, agent_type, max_entries=config.max_decision_entries)` — no separate signal context, signals are embedded in decisions via `is_signal` field.
- Decision writing: `cosmos.write_decision()` produces a doc, then if actionable (SELL for sell-side, ROLL/CLOSE for monitors), `cosmos.write_signal()` is called with the decision_id linkage, and `is_signal=True` is set on the decision payload.
- Refactored `main.py`: scheduler now initializes `CosmosDBService` and `ContextProvider` at `setup()`. `_run_all_agents_async()` passes cosmos+context_provider to all four agent wrappers.
- All four agent wrappers (`covered_call_agent.py`, `cash_secured_put_agent.py`, `open_call_monitor_agent.py`, `open_put_monitor_agent.py`) rewritten: query CosmosDB for enabled symbols/positions, create a shared `TradingViewFetcher` context manager, iterate symbols calling the runner per-symbol.
- Updated `web/app.py` `_run_agent_in_background()` to pass `scheduler.cosmos` and `scheduler.context_provider` to agent wrapper functions.
- TradingView fetcher is now instantiated once per agent wrapper (shared across symbols) via `async with TradingViewFetcher() as fetcher` — fetcher passed to runner methods.
- Agent instructions (tv_*_instructions.py) and tv_data_fetcher.py are UNCHANGED.
- `logger.py` file I/O functions are no longer imported by agent_runner — they remain for any legacy usage but are effectively dead code in the agent pipeline.

### Phase 4 — File-Based Storage Cleanup (2025-07)
- Deleted `data/` directory (old .txt symbol files: covered_call_symbols, cash_secured_put_symbols, opened_calls, opened_puts).
- Deleted `logs/` directory (old .jsonl decision/signal logs for all agent types).
- Deleted `src/logger.py` — deprecated file-based logger, no longer imported anywhere after the CosmosDB migration.
- Deleted `scripts/migrate_to_cosmosdb.py` — one-time migration script, no longer needed now that CosmosDB is the sole data store.
- Updated README.md: fixed Per-symbol Context Filtering config (max_decision_entries: 2, no separate signal config), removed Migration section, removed Azure Files reference, added inline az CLI commands for CosmosDB provisioning, cleaned project structure tree.
- Verified `config.py`, `cosmos_db.py`, and `main.py` compile cleanly — no references to deleted files.

### Phase 5 — Unified Azure Setup Docs (2025-07)
- Merged the separate "Deploy to Azure Container Apps" and "Azure CosmosDB Setup" README sections into a single "Azure Setup" section with a logical flow: variables → resource group → CosmosDB → Container Apps → update deployment.
- Eliminated duplicate resource group creation and inconsistent location defaults (was `swedencentral` for Container Apps, `eastus` for CosmosDB — unified on `eastus`).
- Replaced mixed `export VAR="value"` / `${VAR:-default}` patterns with consistent `${VAR:-default}` throughout.
- Collapsed three CosmosDB setup options (script, inline CLI, portal) into inline CLI commands as the primary path, with a one-line note pointing to the script and portal as alternatives.
- Verified `scripts/provision_cosmosdb.sh` variable names already match the README exactly — no script changes needed.

### Phase 6 — CosmosDB Startup Diagnostics (2025-07)
- The startup handler in `web/app.py` silently set `cosmos = None` when env vars were missing, giving users zero debugging info ("CosmosDB not available" with no reason).
- Added `logging` module usage throughout: `_resolve_env` warns on unset env vars, startup logs endpoint/key-presence/database, success/failure with full tracebacks via `logger.exception()`.
- Stored `app.state.cosmos_error` with specific error reason (missing env var names, or exception string) so the frontend can display it.
- Updated settings page template to show ✅/❌ status badge and error detail when CosmosDB is not connected.
- Added `cosmos.database.read()` call in startup to eagerly validate the connection — `CosmosClient()` is lazy and won't fail until the first real query.
- Updated `_get_cosmos`, dashboard route, and symbol detail page to include the error reason in all error messages.

## Learnings

### UI Template Patterns (2025-07)
- Signal counts/totals live as Jinja2 expressions in subtitles (`signals.html` line 8) and card-badges (`symbols.html` line 31 `card-badge` span).
- The Add Symbol form is in `web/templates/symbols.html` with inline JS in `{% block scripts %}`. Form fields are plain HTML inputs, no framework.
- The JS `fetch('/api/symbols', ...)` POST is what drives symbol creation; the backend is `web/app.py:api_create_symbol()`.
- `display_name` default logic existed only on the frontend (`displayName || ticker`); added server-side fallback in `api_create_symbol` for robustness.
- CC/CSP toggle checkboxes for existing symbols live in the symbols table (toggle-cc / toggle-csp classes); the create form had separate `#newCC` / `#newCSP` inputs.
- Symbol detail page (`web/templates/symbol_detail.html`) now has CC/CSP toggle switches in the page header, replacing the old static badge approach.
- Toggles use the same `PUT /api/symbols/{symbol}` endpoint with `{covered_call: bool}` / `{cash_secured_put: bool}` payloads — same pattern as the list page.
- The `.switch` / `.slider` CSS classes from `web/static/style.css` are available globally via the base template — no extra imports needed.
- Toggle JS uses element IDs (`toggle-cc`, `toggle-csp`) rather than class-based selectors since there's only one instance on the detail page.

### Chat Context Preload Pattern (2025-07)
- Per-symbol chat (`/api/symbols/{symbol}/chat`) was re-fetching CosmosDB + TradingView data on every message (~5-10s latency).
- Extracted `_build_symbol_context()` helper and `_build_symbol_system_prompt()` to DRY up context assembly.
- New `POST /api/symbols/{symbol}/chat/context` endpoint does the heavy lifting once; frontend caches `context` in a JS variable.
- Chat endpoint accepts optional `context` field in request body — skips all fetching when present, falls back to full fetch when absent.
- Frontend disables input/send until context loads, shows "Loading market data..." state, gracefully degrades on fetch failure.
- Key files: `web/app.py` (lines ~953-1095), `web/templates/symbol_chat.html`, `web/templates/symbol_detail.html`.
- Chat icon moved inline with `<h1>` on symbol detail page using `btn-sm` + `font-size:0.5em; vertical-align:middle`.

### Watchlist Toggle Cascade Delete (2025-07)
- When a watchlist toggle (covered_call or cash_secured_put) is set to False via the PUT /api/symbols/{symbol} endpoint, all decisions and linked signals for that agent type on that symbol are now cascade-deleted.
- New method `CosmosDBService.delete_decisions_by_agent_type(symbol, agent_type)` follows the same pattern as `delete_position()`: query decisions → collect IDs → query linked signals → delete signals → delete decisions.
- The cascade runs AFTER the watchlist flag is persisted, so the DB state is consistent even if the cascade fails mid-way (flag is already off, stale data gets cleaned next time).
- Only triggers on explicit `False` — absent fields or `True` values do not trigger deletion.
- Uses parameterized query for `agent_type` but builds a literal IN list for decision IDs (same as `delete_position` — CosmosDB doesn't support parameterized IN).
- Settings page "Debug: TradingView Fetch" symbol dropdown was empty because `settings_page()` in `web/app.py` called `cosmos.get_symbols()` — a method that doesn't exist. The correct method is `cosmos.list_symbols()`. The bare `except Exception: pass` swallowed the AttributeError silently, leaving `symbols` as `[]`.

### README Documentation Update for Data Fetching Methods (2025-07)
- Updated four sections of README.md to reflect current `tv_data_fetcher.py` implementation.
- Overview page: changed from generic `innerText` to targeted `getElementById` extracting 5 specific div sections (`upcoming-earnings`, `key-stats-id`, `employees-section`, `company-info-id`, `financials-overview-id`).
- Options chain: changed from `page.goto` + `click` + `innerText` (DOM scraping) to `page.on("response")` API interception capturing structured JSON from TradingView scanner endpoints, with DOM fallback.
- Added `symbol_detail.html` and `fetch_preview.html` to file tree; added Symbol Detail, Fetch Preview, and updated Settings entries to Web Dashboard section.

### Position-from-Decision Feature (2025-07)
- Extended `add_position()` in `src/cosmos_db.py` with optional `source: dict | None = None` param. When provided, the source snapshot (decision metadata) is embedded in the position document.
- New endpoint `POST /api/symbols/{symbol}/positions/from-decision/{decision_id}` in `web/app.py` (line ~324). Fetches decision via `cosmos.get_decision_by_id()`, maps agent_type → position type (covered_call→call, cash_secured_put→put), builds source snapshot, creates position, then disables watchlist and cascade-deletes related decisions/signals.
- Pattern: endpoint does watchlist disable + cascade delete inline (same as `api_update_symbol` does) rather than factoring into a shared helper — keeps it explicit and avoids coupling.
- Source snapshot captures: decision_id, agent_type, decision, confidence, reason, underlying_price, premium, iv, risk_flags, timestamp — full provenance for the position.

### Position-from-Decision Feature — Backend Integration (2026-03-29)
- Extended `add_position()` in `src/cosmos_db.py` with optional `source: dict | None = None` parameter to track position origin.
- Implemented new endpoint `POST /api/symbols/{symbol}/positions/from-decision/{decision_id}` in `web/app.py` that:
  - Fetches decision via `cosmos.get_decision_by_id(decision_id)`
  - Maps agent_type to position type (covered_call→call, cash_secured_put→put)
  - Builds source snapshot with full provenance (decision_id, agent_type, decision, confidence, reason, underlying_price, premium, iv, risk_flags, timestamp)
  - Creates position with source metadata
  - Performs inline watchlist disable and cascade-delete (same pattern as `api_update_symbol`)
- Design decision: Inline watchlist/cascade logic rather than shared helper keeps flows independent and avoids coupling user action with general symbol updates. Trade-off: logic must be maintained in two places if requirements change.
- Coordinated with Linus (Quant Dev) for frontend button and route integration
- **Status:** ✅ Complete and ready for end-to-end testing

## Cross-Agent Impact

### 2026-03-29: Position-from-Decision Feature (Frontend Integration by Linus)
**From:** Linus (Quant Dev)

Linus completed frontend integration for the position-from-decision workflow:
- Added "Open Position" button to decision_detail.html (signal banner, Jinja conditional for `is_signal`)
- Implemented expandable position rows in symbol_detail.html via hidden `<tr class="pos-detail-row">` with chevron affordance
- Event propagation guard prevents expand/collapse when clicking Close/Delete buttons
- Reused existing CSS classes for visual consistency; added chevron column (8 columns total, colspan=8)

**Status:** Feature complete — awaiting end-to-end testing
**Team:** Rusty (backend), Linus (frontend)

### Roll Position Backend (2025-07)
- Added `roll_position()` to `CosmosDBService` — atomic close-old + create-new in a single `replace_item` call. Old position gets `closed_at`, `closing_source`, and `rolled_to`; new position gets `source`, `rolled_from`. Both linked by position IDs for full traceability.
- Validates old position exists and is active before rolling. Generates new position ID using the standard `pos_{symbol}_{type}_{strike}_{exp}` format.
- New API endpoint `POST /api/symbols/{symbol}/positions/roll-from-decision/{decision_id}` — mirrors the existing `from-decision` pattern but for monitor agents (`open_call_monitor` → call, `open_put_monitor` → put). Same snapshot format for source/closing_source.
- Key difference from "Open Position": no watchlist disable, no cascade-delete. Monitor agents track open positions, not watchlist items.

## Learnings

### 2026-03-29: Domain Entity Rename (decision → activity, signal → alert)
**Scope:** Comprehensive backend rename across all Python files

Systematically renamed domain entities throughout the backend:
- **Function names:** `write_decision()` → `write_activity()`, `write_signal()` → `write_alert()`, `get_recent_decisions()` → `get_recent_activities()`, etc.
- **Variable names:** `decision` → `activity`, `signal` → `alert`, `decision_data` → `activity_data`, `is_signal` → `is_alert`, `_SIGNAL_FIELDS` → `_ALERT_FIELDS`, etc.
- **Dictionary keys:** `"decision"` → `"activity"`, `"signal"` → `"alert"`, `"is_signal"` → `"is_alert"`, `"decision_id"` → `"activity_id"`
- **CosmosDB doc_type values:** `"decision"` → `"activity"`, `"signal"` → `"alert"` (in documents and all query strings)
- **Config keys:** `max_decision_entries` → `max_activity_entries`, `decision_ttl_days` → `activity_ttl_days`
- **Comments/docstrings:** Updated all references to use new terminology

**Files modified:**
- `src/cosmos_db.py` — All CRUD operations, query strings, doc_type values
- `src/agent_runner.py` — All agent execution logic, field constants, helper methods
- `src/context.py` — Context injection provider
- `src/config.py` — Config property names
- `src/main.py` — (no changes needed, only OS signal handling)
- `src/covered_call_agent.py` — Parameter passing
- `src/cash_secured_put_agent.py` — Parameter passing
- `src/open_call_monitor_agent.py` — Parameter passing
- `src/open_put_monitor_agent.py` — Parameter passing
- `config.yaml` — Config section keys and comments
- `scripts/provision_cosmosdb.sh` — Indexing policy field reference

**Database impact:** Database will be recreated from scratch — no backward compatibility needed. All references updated exhaustively.

**Verification:** Zero remaining "decision" or "signal" references in backend files (except OS signal handling in main.py: `import signal`, `SIGINT`, `SIGTERM` — correctly preserved).

**Key insight:** Used systematic sed scripts for bulk renames in large files (cosmos_db.py: 692 lines, agent_runner.py: 606 lines), followed by targeted manual edits for edge cases. Parallel grep verification ensured completeness.

---

## 2025-01-19 — CosmosDB Settings Container with Deep-Merge Logic

**Task:** Persist application configuration in CosmosDB instead of just config.yaml, with automatic seeding on first run and non-destructive merging of new keys on subsequent runs.

**Behavior implemented:**
1. New `settings` container in CosmosDB (partition key: `/id`, single document `app-config`)
2. On first app run (empty container), persist config from config.yaml (excluding credentials) into CosmosDB
3. On subsequent runs, deep-merge adds new keys from config.yaml without overwriting existing CosmosDB values
4. Settings UI reads/writes to CosmosDB (config.yaml as fallback)
5. Telegram notifier reads from CosmosDB (config.yaml as fallback)

**Implementation details:**

**cosmos_db.py:**
- Added `settings_container` client initialization (best-effort pattern, like telemetry)
- Implemented `get_settings()` → returns stored settings or empty dict
- Implemented `save_settings(settings: dict)` → upserts full settings document
- Implemented `merge_defaults(defaults: dict)` → recursive deep-merge logic:
  - Key in defaults but not in stored → add it
  - Key in both and both are dicts → recurse
  - Key in both and stored value NOT a dict → keep stored (never overwrite)
  - Key in stored but not in defaults → keep it
- Settings document structure: `{id: "app-config", context: {...}, scheduler: {...}, web: {...}, telegram: {...}}`
- Credentials (`azure`, `cosmosdb` sections) intentionally excluded (chicken-and-egg problem)

**main.py:**
- After creating `CosmosDBService`, call `merge_defaults()` with non-credential config sections
- Pass `cosmos` to `TelegramNotifier` constructor

**telegram_notifier.py:**
- Added `cosmos` parameter to `__init__`
- Updated `_get_credentials()` to try CosmosDB first, fall back to config.yaml
- Docstring updated to reflect new behavior

**web/app.py:**
- Added helper functions `_load_settings_from_cosmos()` and `_save_settings_to_cosmos()`
- Settings GET handler: reads from CosmosDB first (falls back to config.yaml if unavailable)
- Settings POST handler: writes to CosmosDB first, also writes to config.yaml (backward compat)
- `init_cosmos()`: after creating CosmosDBService, calls `merge_defaults()` with resolved config (web-only mode support)
- Cron reschedule logic still works (scheduler reference on app.state)

**provision_cosmosdb.sh:**
- Added settings container creation section (partition key `/id`, serverless + provisioned variants)

**Key design choices:**
- Best-effort pattern: missing settings container logs warning, falls back to config.yaml
- Deep-merge is recursive: handles nested dicts correctly
- Config.yaml writes preserved for backward compat (env vars not yet in config can still be resolved)
- Env vars resolved before storing in CosmosDB (Settings UI saves actual values, not placeholders)
- Settings UI changes now take effect immediately for all components (no restart needed)

**Files modified:**
- `src/cosmos_db.py` — Settings container + get/save/merge methods
- `src/main.py` — Call merge_defaults at startup, pass cosmos to TelegramNotifier
- `src/telegram_notifier.py` — Read from CosmosDB first
- `web/app.py` — Settings GET/POST read/write from CosmosDB, init_cosmos merges defaults
- `scripts/provision_cosmosdb.sh` — Add settings container creation
- `README.md` — Document settings container and persistence behavior

**Verification:** All Python files validated with `py_compile`. Syntax clean. Commit: fa64388.

**Key insight:** The recursive deep-merge pattern ensures graceful config evolution across deployments — new features can add config keys in config.yaml without disrupting existing user settings in CosmosDB. The dual-write strategy (CosmosDB primary, config.yaml backup) provides resilience when CosmosDB is temporarily unavailable.

---

## 2025-01-XX: Timezone Configuration Support

**Task:** Add timezone awareness to the scheduler backend (default: America/New_York).

**Changes:**

**config.yaml:**
- Added `timezone: "America/New_York"` field under `scheduler` section
- Provides default timezone for cron scheduling

**src/config.py:**
- Imported `pytz` module for timezone validation
- Added `timezone` property getter:
  - Reads from `config.scheduler.timezone`
  - Defaults to "America/New_York" if not set
  - Validates with `pytz.timezone()`, falls back to default if invalid
  - Returns timezone string (e.g., "America/New_York")
- Added `timezone` setter:
  - Validates timezone string with `pytz.timezone()`
  - Raises `ValueError` if invalid
  - Updates `config['scheduler']['timezone']`

**src/main.py:**
- Imported `pytz` module
- Updated `reschedule()` method:
  - Added optional `new_timezone` parameter
  - Updates timezone config if provided
- Updated `setup()` method:
  - Added timezone logging: `print(f"Scheduler timezone: {self.config.timezone}")`
- Updated `_run_all_agents_async()`:
  - Gets timezone-aware datetime: `now_tz = datetime.now(pytz.timezone(self.config.timezone))`
  - Logs timestamps with timezone abbreviation: `%Y-%m-%d %H:%M:%S %Z`
- Updated `run()` main scheduler loop:
  - Creates timezone object: `tz = pytz.timezone(self.config.timezone)`
  - Uses timezone-aware datetime for croniter: `croniter(cron_expr, now_tz)`
  - All datetime comparisons now timezone-aware
  - Reschedule logic recreates timezone object when `_cron_changed` flag is set
  - Logs include timezone abbreviation (EDT, EST, etc.)

**requirements.txt:**
- Added `pytz>=2024.0` explicit dependency

**Key patterns:**
- Timezone validation happens at config property access (fail-fast with fallback)
- All cron scheduling uses timezone-aware datetime objects
- Scheduler loop checks timezone on reschedule (supports dynamic timezone changes)
- CosmosDB settings persistence: timezone stored alongside cron expression (web app handles automatically via merge_defaults)
- Timezone abbreviation (%Z) in logs helps debugging across daylight saving transitions

**Files modified:**
- `config.yaml` — Added timezone field
- `src/config.py` — Added timezone property with pytz validation
- `src/main.py` — Timezone-aware scheduling throughout
- `requirements.txt` — Added pytz dependency

**Verification:** Python syntax validated with `py_compile`. All files compile cleanly.

**Key insight:** The pytz library handles all timezone complexity (daylight saving, historical changes, etc.). By making croniter timezone-aware from the start, we ensure schedule accuracy regardless of server timezone or DST transitions. The existing CosmosDB settings persistence automatically handles timezone storage/retrieval — no schema changes needed.

---

## 2025-01-XX: Dashboard Timezone Display

**Task:** Update dashboard API to show last_run and next_run in scheduler's configured timezone (not server local time or UTC).

**Problem:**
- Dashboard was calculating `next_run` using `datetime.now()` (server local time)
- `last_run` was displaying raw UTC timestamp from activities
- No timezone information sent to frontend
- Users in different timezones couldn't tell when scheduler actually ran/will run

**Solution:**

**web/app.py changes:**

1. **Added pytz import** (line 12)
   - Required for timezone conversion

2. **Dashboard route — timezone-aware next_run calculation:**
   - Load scheduler timezone from config: `config.get("scheduler", {}).get("timezone", "America/New_York")`
   - Create timezone object with fallback: `scheduler_tz = pytz.timezone(scheduler_tz_str)`
   - Calculate next_run using scheduler timezone: `now_tz = datetime.now(scheduler_tz)`
   - Format with timezone abbreviation: `strftime("%Y-%m-%d %H:%M:%S %Z")` → "2025-01-15 14:30:00 EST"
   - Also provide ISO format: `next_run_iso = next_run_dt.isoformat()`

3. **Dashboard route — timezone-aware last_run conversion:**
   - Parse activity timestamp (stored in UTC): `datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))`
   - Ensure timezone awareness: `replace(tzinfo=timezone.utc)` if naive
   - Convert to scheduler timezone: `last_run_dt.astimezone(scheduler_tz)`
   - Format with timezone abbreviation: `strftime("%Y-%m-%d %H:%M:%S %Z")`
   - Provide ISO format: `last_run_iso = last_run_dt.isoformat()`

4. **Template context additions:**
   - `last_run`: Human-readable string with timezone (e.g., "2025-01-15 14:30:00 EST")
   - `last_run_iso`: ISO 8601 with timezone for JavaScript parsing
   - `next_run`: Human-readable string with timezone
   - `next_run_iso`: ISO 8601 with timezone for JavaScript parsing
   - `scheduler_timezone`: Timezone string (e.g., "America/New_York") for frontend display/conversion

**Key patterns:**
- **Server-side conversion preferred:** Convert to scheduler timezone in Python (more reliable than JavaScript timezone handling)
- **Dual format strategy:** Send both human-readable (with %Z abbreviation) and ISO format (for programmatic use)
- **Graceful fallback:** Invalid timezone config falls back to America/New_York
- **Consistent timezone source:** Uses same config path as scheduler (`config.scheduler.timezone`)
- **Timezone abbreviation in display:** Shows "EST" or "EDT" automatically based on date (pytz handles DST)

**Frontend usage:**
- Display `last_run` and `next_run` directly (already in scheduler timezone)
- Use `last_run_iso`/`next_run_iso` for calculations or relative time display
- Show `scheduler_timezone` to clarify which timezone times are displayed in
- Can use JavaScript `Intl.DateTimeFormat` with `last_run_iso` to convert to user's browser timezone if desired

**Files modified:**
- `web/app.py` — Added pytz import, timezone-aware time calculations in dashboard route

**Verification:** Python syntax validated with `py_compile`. Clean compilation.

**Key insight:** By converting to scheduler timezone on the backend, we ensure consistent display regardless of browser timezone. The ISO format provides flexibility for frontend to do additional conversions if needed. The %Z formatter automatically shows correct timezone abbreviation (EST vs EDT) based on DST rules, which is more reliable than trying to compute this in JavaScript.
