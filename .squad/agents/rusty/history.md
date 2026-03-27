# Rusty — History

## Project Context
- **Project:** options-agent
- **User:** dsanchor
- **Stack:** Python, Microsoft Agent Framework, Azure Foundry (gpt-5.1)
- **MCP:** iflow-mcp_ferdousbhai_investor-agent 1.6.3
- **Description:** Two periodic trading agents for covered call and cash-secured put sell signals. Local runtime, configurable polling, stock symbols from files, decision logs, sell signal alerts.

## Learnings

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

