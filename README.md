# Options Trading Agent

Periodic options trading analysis using Microsoft Agent Framework with MCP integration.

## Architecture

Two specialized agents analyze options trading opportunities:
- **Covered Call Agent**: Analyzes stocks for covered call writing opportunities
- **Cash Secured Put Agent**: Analyzes stocks for cash secured put opportunities

Both agents use the Microsoft Agent Framework (`agent-framework`) with MCP (Model Context Protocol) stdio integration to access real-time market data and options pricing. Two data providers are supported — switch between them in `config.yaml`:

| Provider | MCP Server | Key Features |
|----------|-----------|--------------|
| **Massive.com** (`massive`) | `mcp_massive` (local stdio) | SQL querying, built-in Black-Scholes Greeks, composable API (search → call → query) |
| **Alpha Vantage** (`alphavantage`) | Remote MCP at `mcp.alphavantage.co` (streamable HTTP) | 50+ tools, built-in technical indicators (RSI, BBANDS, MACD), progressive tool discovery, news sentiment scores |

## Prerequisites

1. **Python 3.12+**
2. **Azure AI Foundry Project** with access to `gpt-5.4-mini` model
3. **Azure Authentication** - Ensure you're logged in via Azure CLI:
   ```bash
   az login
   ```
4. **[Astral UV](https://docs.astral.sh/uv/getting-started/installation/)** (v0.4.0+) - For installing the Massive.com MCP server
5. **API Key** for your chosen data provider:
   - **Massive.com** — [Get one free at massive.com](https://massive.com/?utm_campaign=mcp&utm_medium=referral&utm_source=github)
   - **Alpha Vantage** — [Get a free key at alphavantage.co](https://www.alphavantage.co/support/#api-key)

## Setup

### 1. Create Virtual Environment and Install Dependencies

```bash
python -m venv venv
source venv/bin/activate 
pip install -r requirements.txt
```

This installs:
- `agent-framework[foundry]` - Microsoft Agent Framework with Foundry support
- `azure-identity` - Azure authentication
- `pyyaml`, `schedule`, `python-dotenv` - Configuration and scheduling

### 2. Install the MCP Financial Data Server

**Massive.com** (local stdio — requires pre-install):
```bash
uv tool install "mcp_massive @ git+https://github.com/massive-com/mcp_massive@v0.8.7"
```
> **Note:** If your system Python lacks sqlite3 support, install with a uv-managed Python:
> `uv tool install --python 3.13 "mcp_massive @ git+https://github.com/massive-com/mcp_massive@v0.8.7"`

**Alpha Vantage** (remote MCP server — no local install needed):
Alpha Vantage uses a hosted MCP server at `mcp.alphavantage.co`. No local dependencies are required — just set your `ALPHAVANTAGE_API_KEY` and select `alphavantage` in `config.yaml`.

### 3. Configure Environment Variables

Set your Azure AI Project endpoint and the API key for your selected provider:

```bash
export AZURE_AI_PROJECT_ENDPOINT="https://your-project.services.ai.azure.com"

# For Massive.com provider:
export MASSIVE_API_KEY="your-massive-api-key"

# For Alpha Vantage provider:
export ALPHAVANTAGE_API_KEY="your-alphavantage-api-key"
```

> You only need the API key for the provider you've selected in `config.yaml`.

### 4. MCP Server Configuration

The MCP server is launched automatically as a subprocess when agents run. Switch providers by changing `mcp.provider` in `config.yaml`:

```yaml
mcp:
  provider: "massive"  # Options: "massive" or "alphavantage"
  massive:
    command: "mcp_massive"
    args: []
    description: "Massive.com financial data API..."
    env_key: "MASSIVE_API_KEY"
  alphavantage:
    transport: "streamable_http"
    url: "https://mcp.alphavantage.co/mcp?apikey=${ALPHAVANTAGE_API_KEY}"
    description: "Alpha Vantage remote MCP server..."
    env_key: "ALPHAVANTAGE_API_KEY"
```

Massive.com uses stdio transport (local subprocess). Alpha Vantage uses streamable HTTP transport (remote server). The transport type is auto-detected from config — stdio providers need `command`+`args`, HTTP providers need `url`.

### 5. Configure Symbols

Edit the symbol files to analyze your desired stocks:

- `data/covered_call_symbols.txt` - Stocks for covered call analysis
- `data/cash_secured_put_symbols.txt` - Stocks for cash secured put analysis

One symbol per line.

### 6. Adjust Configuration (Optional)

Edit `config.yaml` to customize:
- `scheduler.interval_minutes` - How often agents run (default: 60 minutes)
- `azure.model_deployment` - Model to use (default: gpt-5.4-mini)
- `mcp.command` and `mcp.args` - MCP server launch command
- Log file paths

## Running

Start the scheduler:

```bash
python -m src.main
```

The agents will:
1. Run immediately on startup
2. Continue running every N minutes (configured in `config.yaml`)
3. Log decisions to `logs/` directory
4. Log clear sell signals separately for easy review

Press `Ctrl+C` to stop gracefully.

## Output

### Decision Logs
- `logs/covered_call_decisions.log` - All covered call analysis results
- `logs/cash_secured_put_decisions.log` - All cash secured put analysis results

### Signal Logs
- `logs/covered_call_signals.log` - Only clear SELL signals for covered calls
- `logs/cash_secured_put_signals.log` - Only clear SELL signals for cash secured puts

## Project Structure

```
options-agent/
├── config.yaml                      # Configuration (provider selection here)
├── src/
│   ├── __init__.py
│   ├── main.py                      # Entry point & scheduler
│   ├── config.py                    # Config loader (multi-provider support)
│   ├── agent_runner.py              # Azure AI agent execution
│   ├── covered_call_agent.py        # Covered call runner (selects instructions by provider)
│   ├── cash_secured_put_agent.py    # Cash secured put runner (selects instructions by provider)
│   ├── covered_call_instructions.py       # Massive.com covered call instructions
│   ├── cash_secured_put_instructions.py   # Massive.com cash secured put instructions
│   ├── av_covered_call_instructions.py    # Alpha Vantage covered call instructions
│   ├── av_cash_secured_put_instructions.py # Alpha Vantage cash secured put instructions
│   └── logger.py                    # Log management
├── data/
│   ├── covered_call_symbols.txt     # CC symbols
│   └── cash_secured_put_symbols.txt # CSP symbols
├── logs/                            # Created at runtime
├── requirements.txt
└── README.md
```

## Troubleshooting

### "Environment variable AZURE_AI_PROJECT_ENDPOINT not set"
Make sure you've exported the environment variable with your Azure AI Foundry project endpoint.

### MCP Server Launch Errors
- **Massive.com**: Ensure `mcp_massive` is installed: `uv tool install "mcp_massive @ git+https://github.com/massive-com/mcp_massive@v0.8.7"`
- **Alpha Vantage**: Uses remote server — ensure `ALPHAVANTAGE_API_KEY` is set and you have network connectivity
- Verify the correct API key env var is set for your provider (`MASSIVE_API_KEY` or `ALPHAVANTAGE_API_KEY`)
- For stdio providers, check that the command is available in PATH
- View detailed MCP logs in the agent output

### `ModuleNotFoundError: No module named '_sqlite3'`
The `mcp_massive` server requires Python's `sqlite3` module. Some system Python builds (e.g., Docker images, custom builds) lack it because `libsqlite3-dev` was not present at compile time. Fix by reinstalling with a uv-managed Python:
```bash
uv tool install --reinstall --python 3.13 "mcp_massive @ git+https://github.com/massive-com/mcp_massive@v0.8.7"
```

### Authentication Errors
Run `az login` and ensure you have access to the Azure AI Foundry project.

### Module Import Errors
Make sure you installed the correct SDK: `pip install agent-framework[foundry]` (NOT `azure-ai-agents`)

## Development

The agent instructions are defined in separate files per provider:
- `src/covered_call_instructions.py` — Covered call instructions (Massive.com)
- `src/cash_secured_put_instructions.py` — Cash secured put instructions (Massive.com)
- `src/av_covered_call_instructions.py` — Covered call instructions (Alpha Vantage)
- `src/av_cash_secured_put_instructions.py` — Cash secured put instructions (Alpha Vantage)

The trading strategy logic is identical across providers — only the DATA GATHERING PROTOCOL differs to match each MCP server's tool interface.

## Technical Details

### SDK Migration
This project uses the **Microsoft Agent Framework** (`agent-framework` package from https://github.com/microsoft/agent-framework).

Key components:
- `agent_framework.Agent` - Main agent class
- `agent_framework.foundry.FoundryChatClient` - Azure AI Foundry integration
- `agent_framework.MCPStdioTool` - MCP server integration via stdio subprocess
- `agent_framework.MCPStreamableHTTPTool` - MCP server integration via streamable HTTP (remote servers)

Massive.com uses stdio transport (local subprocess). Alpha Vantage uses streamable HTTP transport (remote server at `mcp.alphavantage.co`). All tool calls are auto-approved (`approval_mode="never_require"`).
