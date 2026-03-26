# Options Trading Agent

Periodic options trading analysis using Microsoft Agent Framework with MCP integration.

## Architecture

Two specialized agents analyze options trading opportunities:
- **Covered Call Agent**: Analyzes stocks for covered call writing opportunities
- **Cash Secured Put Agent**: Analyzes stocks for cash secured put opportunities

Both agents use the Microsoft Agent Framework (`agent-framework`) with MCP (Model Context Protocol) stdio integration to access real-time market data and options pricing via the `iflow-mcp_ferdousbhai_investor-agent` MCP server.

## Prerequisites

1. **Python 3.9+**
2. **Azure AI Foundry Project** with access to `gpt-5.4-mini` model
3. **Azure Authentication** - Ensure you're logged in via Azure CLI:
   ```bash
   az login
   ```
4. **uvx** - For running the MCP server (installed automatically with `uv` or available standalone)

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

This installs:
- `agent-framework[foundry]` - Microsoft Agent Framework with Foundry support
- `azure-identity` - Azure authentication
- `pyyaml`, `schedule`, `python-dotenv` - Configuration and scheduling

### 2. Configure Environment Variables

Set your Azure AI Project endpoint:

```bash
export AZURE_AI_PROJECT_ENDPOINT="https://your-project.services.ai.azure.com"
```

### 3. MCP Server Configuration

The MCP server (`iflow-mcp_ferdousbhai_investor-agent`) is launched automatically as a subprocess via `uvx` when agents run. No separate server startup needed!

Configuration is in `config.yaml`:
```yaml
mcp:
  command: "uvx"
  args: ["iflow-mcp_ferdousbhai_investor-agent"]
  description: "Financial analysis MCP server for stock data, options chains, and market sentiment"
```

### 4. Configure Symbols

Edit the symbol files to analyze your desired stocks:

- `data/covered_call_symbols.txt` - Stocks for covered call analysis
- `data/cash_secured_put_symbols.txt` - Stocks for cash secured put analysis

One symbol per line.

### 5. Adjust Configuration (Optional)

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
├── config.yaml                      # Configuration
├── src/
│   ├── __init__.py
│   ├── main.py                      # Entry point & scheduler
│   ├── config.py                    # Config loader
│   ├── agent_runner.py              # Azure AI agent execution
│   ├── covered_call_agent.py        # Covered call runner
│   ├── cash_secured_put_agent.py    # Cash secured put runner
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
- Ensure `uvx` is installed (`pip install uv` or `pipx install uv`)
- Check that `iflow-mcp_ferdousbhai_investor-agent` is available via uvx
- View detailed MCP logs in the agent output

### Authentication Errors
Run `az login` and ensure you have access to the Azure AI Foundry project.

### Module Import Errors
Make sure you installed the correct SDK: `pip install agent-framework[foundry]` (NOT `azure-ai-agents`)

## Development

The agent instructions are defined in separate files:
- `src/covered_call_instructions.py` - Covered call agent instructions
- `src/cash_secured_put_instructions.py` - Cash secured put agent instructions

Modify these files to customize agent behavior and analysis criteria.

## Technical Details

### SDK Migration
This project uses the **Microsoft Agent Framework** (`agent-framework` package from https://github.com/microsoft/agent-framework).

Key components:
- `agent_framework.Agent` - Main agent class
- `agent_framework.foundry.FoundryChatClient` - Azure AI Foundry integration
- `agent_framework.MCPStdioTool` - MCP server integration via stdio subprocess

The MCP server is launched as a subprocess and communicates via stdio, not HTTP. All tool calls are auto-approved (`approval_mode="never_require"`).
