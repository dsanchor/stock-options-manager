# Stock Options Manager

Periodic options trading analysis using Microsoft Agent Framework with MCP integration.

## Architecture

Four specialized agents handle options trading:
- **Covered Call Agent**: Analyzes stocks for covered call writing opportunities
- **Cash Secured Put Agent**: Analyzes stocks for cash secured put opportunities
- **Open Call Monitor**: Monitors open covered call positions for assignment risk
- **Open Put Monitor**: Monitors open cash-secured put positions for assignment risk

The first two agents (sell-side) decide whether to **open** new positions. The last two (position monitors) decide whether to **hold or adjust** existing positions.

Both sell-side agents use the Microsoft Agent Framework (`agent-framework`) with TradingView as the data source. Market data is pre-fetched deterministically via [Playwright MCP](https://github.com/microsoft/playwright-mcp) (headless browser) and passed to the LLM for analysis — the LLM never touches the browser directly.

## How It Works

End-to-end flow for each scheduled run:

```
Scheduler (main.py)
  │
  ├─ Covered Call Agent
  │    for each symbol in data/covered_call_symbols.txt:
  │      1. Load per-symbol context (past decisions + signals)
  │      2. Pre-fetch TradingView data (overview, technicals, forecast, options chain)
  │      3. LLM analyzes pre-fetched data → structured JSON decision
  │      4. Log decision to JSONL; if SELL → also log to signal file
  │
  ├─ Cash Secured Put Agent
  │    (same loop, different symbols file + instructions)
  │
  ├─ Open Call Monitor
  │    for each position in data/opened_calls.txt:
  │      1. Parse position (symbol, strike, expiration)
  │      2. Pre-fetch TradingView data
  │      3. LLM assesses assignment risk → WAIT or ROLL decision
  │      4. Log decision; if ROLL/CLOSE → also log to signal file
  │
  └─ Open Put Monitor
       (same loop, different positions file + instructions)
```

**Data gathering:** Python pre-fetches ALL TradingView data deterministically — overview, technicals, forecast, and options chain — using the Playwright MCP server driven from `tv_data_fetcher.py`. The LLM never touches the browser. It receives the data as text and only performs analysis. See [Pre-fetch Architecture](#pre-fetch-architecture-tradingview) below.

**Per-symbol context injection:** Before each symbol is analyzed, the runner reads that symbol's recent decisions and signals from the JSONL logs and injects them into the prompt. The LLM sees only context for the symbol it's currently analyzing — not a mix of all symbols. Context limits are configurable in `config.yaml` (`context.max_decision_entries`, `context.max_signal_entries`).

**Output:** Every symbol produces a decision (SELL, WAIT, or HOLD) written to the decision log. Only SELL decisions are additionally written to the signal log — the actionable alerts that downstream systems watch. Position monitors produce WAIT or ROLL decisions, with ROLL/CLOSE written to their signal logs.

## Key Concepts

### Decision vs Signal

**Sell-side agents (Covered Call, Cash Secured Put):**
A **decision** is recorded for EVERY symbol on EVERY run. Possible values: `SELL`, `WAIT`, or `HOLD`. The decision log is the complete audit trail. A **signal** is the subset of decisions where the action is `SELL` — the actionable alerts.

**Position monitors (Open Call Monitor, Open Put Monitor):**
A **decision** is recorded for EVERY position on EVERY run. Possible values: `WAIT`, `ROLL_UP`, `ROLL_DOWN`, `ROLL_OUT`, `ROLL_UP_AND_OUT`, `ROLL_DOWN_AND_OUT`, or `CLOSE`. A **signal** is any decision that is NOT `WAIT` — any roll or close action that requires attention.

### Open Position Monitors

The Open Call Monitor and Open Put Monitor watch **existing** short options positions for assignment risk. They differ from the sell-side agents in several ways:

| | Sell-Side Agents | Position Monitors |
|---|---|---|
| **Input** | Symbol list (`EXCHANGE-SYMBOL`) | Position file (`EXCHANGE-SYMBOL,strike,expiration`) |
| **Decisions** | SELL / WAIT | WAIT / ROLL_UP / ROLL_DOWN / ROLL_OUT / ROLL_UP_AND_OUT / ROLL_DOWN_AND_OUT / CLOSE |
| **Signals** | SELL only | Any ROLL or CLOSE |
| **Focus** | "Should I open a new position?" | "Is my existing position safe?" |

**Position file format** (`data/opened_calls.txt` or `data/opened_puts.txt`):
```
# Open covered call positions (EXCHANGE-SYMBOL,strike,expiration)
NYSE-MO,72,2026-04-24
NASDAQ-AAPL,200,2026-05-16
```
Lines starting with `#` are comments. Empty lines are skipped. If the file has no uncommented lines, the monitor skips gracefully.

**Profit optimization:** When ALL market indicators unanimously show the position is deeply OTM with no risk catalysts, the monitor may recommend tightening the strike to collect additional premium (ROLL_DOWN for calls, ROLL_UP for puts). This requires unanimous indicator agreement across 9 conditions — conservative by design. Profit-optimization rolls are tagged with a `"profit_optimization"` risk flag to distinguish them from defensive rolls.

**Roll types:**
- **ROLL_UP** — Higher strike, same expiration (gives more room above for calls)
- **ROLL_DOWN** — Lower strike, same expiration (gives more room below for puts)
- **ROLL_OUT** — Same strike, later expiration (more time value)
- **ROLL_UP_AND_OUT** / **ROLL_DOWN_AND_OUT** — Combined strike + expiration adjustment
- **CLOSE** — Buy back without re-selling (exit the position entirely)

### Pre-fetch Architecture (TradingView)

LLMs don't reliably make multi-step browser tool calls. When given Playwright tools directly, they skip pages, fabricate navigation errors, and ignore sequencing instructions.

The solution: `TradingViewFetcher` (`src/tv_data_fetcher.py`) drives the Playwright MCP server from Python — deterministically, with no LLM involvement. It fetches four pages per symbol:

| Page | Method | Typical Size | Content |
|------|--------|-------------|---------|
| Overview | `browser_run_code` (innerText) | ~variable | Current price, market cap, P/E ratio, dividend yield, 52-week range, volume, sector, industry, earnings date |
| Technicals | `browser_run_code` (innerText) | ~3K chars | RSI, MACD, Stochastic, all MAs (10-200), pivot points (R1-R3, S1-S3) with Buy/Sell/Neutral signals |
| Forecast | `browser_run_code` (innerText) | ~2.5K chars | Analyst consensus, price targets, EPS history, revenue data |
| Options chain | `browser_navigate` + `click` + `snapshot` | ~65K chars | Full chain expanded to best 30-45 DTE expiration via accessibility snapshot |

The agent is created with **no tools** — it only analyzes the pre-fetched data included in its prompt. This is the key pattern: move deterministic multi-step workflows to the host language; let the LLM do what it's good at — analysis.

### Per-symbol Context Filtering

Each symbol's analysis only sees its OWN prior decisions and signals. The logger reads the JSONL file, filters entries by the `symbol` field, and returns only matching entries up to the configured limit. This prevents cross-contamination between symbols and keeps context focused.

Configurable in `config.yaml`:
```yaml
context:
  max_decision_entries: 5   # Recent decisions injected per symbol
  max_signal_entries: 1     # Recent signals injected per symbol
```

### JSONL Output Format

All output is [JSON Lines](https://jsonlines.org/) — one JSON object per line. Machine-parseable for downstream automation. Files use the `.jsonl` extension.

## Prerequisites

1. **Python 3.12+**
2. **Azure AI Foundry Project** with access to a model deployment (e.g. `gpt-5.1`, `gpt-5.4-mini`)
3. **Azure OpenAI API Key** - Get your API key from Azure Portal
4. **[Node.js](https://nodejs.org/)** - Required for the Playwright MCP server (runs via `npx`)

## Setup

### 1. Create Virtual Environment and Install Dependencies

```bash
python -m venv venv
source venv/bin/activate 
pip install -r requirements.txt
```

This installs:
- `agent-framework[foundry]` - Microsoft Agent Framework with Foundry support
- `pyyaml`, `croniter`, `python-dotenv` - Configuration and scheduling

### 2. Install the Playwright MCP Server

TradingView data is fetched via the [Playwright MCP server](https://github.com/microsoft/playwright-mcp) running locally via `npx`. No API key needed.

```bash
# Test it works (first run downloads Playwright + Chromium automatically):
npx @playwright/mcp@latest --help
```

> Playwright MCP bundles Chromium and fully renders JavaScript, so options chains, financials, and all dynamic content are available.

### 3. Configure Environment Variables

Set your Azure AI Project endpoint and API key:

```bash
export AZURE_AI_PROJECT_ENDPOINT="https://your-project.services.ai.azure.com"
export MODEL_DEPLOYMENT="gpt-5.1"  # or "gpt-5.4-mini"
export AZURE_OPENAI_API_KEY="your-api-key-here"

# No API key needed for TradingView — data is free via Playwright browser automation
```

### 4. MCP Server Configuration

The Playwright MCP server is launched automatically as a subprocess via `npx` when agents run. Configure in `config.yaml`:

```yaml
mcp:
  command: "npx"
  args: ["@playwright/mcp@latest"]
  description: "Playwright MCP server for browser automation..."
```

### 5. Configure Symbols

Edit the symbol files to analyze your desired stocks:

- `data/covered_call_symbols.txt` - Stocks for covered call analysis
- `data/cash_secured_put_symbols.txt` - Stocks for cash secured put analysis

One symbol per line, in `EXCHANGE-SYMBOL` format:
```
# Format: EXCHANGE-SYMBOL
NASDAQ-AAPL
NYSE-AA
NASDAQ-MSFT
```

The exchange prefix is used to construct TradingView URLs (e.g., `NASDAQ-AAPL` → `https://www.tradingview.com/symbols/NASDAQ-AAPL/`).

**Position files** (for Open Position Monitors):

- `data/opened_calls.txt` - Open covered call positions to monitor
- `data/opened_puts.txt` - Open cash-secured put positions to monitor

One position per line, in `EXCHANGE-SYMBOL,strike,expiration` format:
```
# Open covered call positions (EXCHANGE-SYMBOL,strike,expiration)
NYSE-MO,72,2026-04-24
NASDAQ-AAPL,200,2026-05-16
```

Position monitors only run when there are uncommented lines in the file. Start with all lines commented out and uncomment when you have open positions to track.

### 6. Adjust Configuration (Optional)

Edit `config.yaml` to customize:

```yaml
azure:
  project_endpoint: "${AZURE_AI_PROJECT_ENDPOINT}"
  model_deployment: "${MODEL_DEPLOYMENT}"  # From env variable (e.g. gpt-5.1, gpt-5.4-mini)

mcp:
  command: "npx"
  args: ["@playwright/mcp@latest"]
  description: "Playwright MCP server for browser automation..."

context:
  max_decision_entries: 5               # Recent decisions injected per symbol
  max_signal_entries: 1                 # Recent signals injected per symbol

scheduler:
  cron: "0 9-16/2 * * 1-5"               # Cron expression (e.g. every 2h, Mon-Fri 9am-4pm)

covered_call:
  symbols_file: "data/covered_call_symbols.txt"
  decision_log: "logs/covered_call_decisions.jsonl"
  signal_log: "logs/covered_call_signals.jsonl"

cash_secured_put:
  symbols_file: "data/cash_secured_put_symbols.txt"
  decision_log: "logs/cash_secured_put_decisions.jsonl"
  signal_log: "logs/cash_secured_put_signals.jsonl"

open_call_monitor:
  positions_file: "data/opened_calls.txt"
  decision_log: "logs/open_call_monitor_decisions.jsonl"
  signal_log: "logs/open_call_monitor_signals.jsonl"

open_put_monitor:
  positions_file: "data/opened_puts.txt"
  decision_log: "logs/open_put_monitor_decisions.jsonl"
  signal_log: "logs/open_put_monitor_signals.jsonl"
```

## Running

### Full app (web dashboard + scheduler)

```bash
python run.py
```

Opens the dashboard at http://localhost:8000 and starts the agent scheduler in a background thread. Press `Ctrl+C` to stop both.

### Web dashboard only

```bash
python run.py --web-only
```

### Scheduler only (no web UI)

```bash
python run.py --scheduler-only
```

### Options

| Flag | Description |
|------|-------------|
| `--web-only` | Start only the web dashboard (no scheduler) |
| `--scheduler-only` | Start only the scheduler (no web) |
| `--port PORT` | Override the web server port (default: from `config.yaml` or 8000) |

The dashboard runs on `http://localhost:8000` by default (configurable in `config.yaml` under `web:`).

### Running with Docker

Build the image (pre-installs Playwright + Chromium):

```bash
docker build -t options-agent .
```

Run with volume mounts for data persistence:

```bash
docker run -d --name options-agent \
  -p 8000:8000 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  -e AZURE_AI_PROJECT_ENDPOINT="https://your-project.services.ai.azure.com" \
  -e MODEL_DEPLOYMENT="gpt-5.1" \
  -e AZURE_OPENAI_API_KEY="your-api-key-here" \
  options-agent
```

| Mount / Variable | Purpose |
|---|---|
| `data/` | Watchlist and position files (user-editable) |
| `logs/` | JSONL decision and signal logs (persisted across restarts) |
| `AZURE_AI_PROJECT_ENDPOINT` | Azure AI Foundry project endpoint |
| `MODEL_DEPLOYMENT` | Model name (e.g. `gpt-5.1`, `gpt-5.4-mini`) |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API key for authentication |

View logs:

```bash
docker logs -f options-agent
```

Pass flags (e.g. web-only mode):

```bash
docker run -d --name options-agent-web \
  -p 8000:8000 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/logs:/app/logs \
  -e AZURE_AI_PROJECT_ENDPOINT="..." \
  -e MODEL_DEPLOYMENT="gpt-5.1" \
  -e AZURE_OPENAI_API_KEY="your-api-key-here" \
  options-agent --web-only
```

**Pages:**
- **Dashboard** (`/`) — Signals overview by agent type with time-range counts, scheduler status, recent activity feed, and position summary. Auto-refresh toggle (60s).
- **Signal Details** (`/signals/{agent}/{symbol}`) — All signals for a specific symbol, newest first, with decision badges and risk flags.
- **Signal + Decisions** (`/signals/{agent}/{symbol}/{index}`) — Full signal JSON and backing decisions from the same time window.
- **Settings** (`/settings`) — Edit watchlist and position files directly. Changes take effect on the next scheduler tick (data files are re-read on every cron run).
- **Chat** (`/chat`) — Ask questions about your portfolio. Uses the same Azure OpenAI model with recent decisions as context.

## Output

All logs use [JSONL format](#jsonl-output-format) — one JSON object per line.

### Decision Logs (complete audit trail)
- `logs/covered_call_decisions.jsonl` - All covered call analysis results
- `logs/cash_secured_put_decisions.jsonl` - All cash secured put analysis results
- `logs/open_call_monitor_decisions.jsonl` - All open call position monitor results
- `logs/open_put_monitor_decisions.jsonl` - All open put position monitor results

### Signal Logs (actionable alerts only)
- `logs/covered_call_signals.jsonl` - Only SELL signals for covered calls
- `logs/cash_secured_put_signals.jsonl` - Only SELL signals for cash secured puts
- `logs/open_call_monitor_signals.jsonl` - Only ROLL/CLOSE signals for open calls
- `logs/open_put_monitor_signals.jsonl` - Only ROLL/CLOSE signals for open puts

### Example Decision Object

Each line in a `.jsonl` log is a self-contained JSON object:

```json
{
  "timestamp": "2026-03-27T00:00:00Z",
  "symbol": "MO",
  "exchange": "NYSE",
  "agent": "covered_call",
  "decision": "WAIT",
  "strike": null,
  "expiration": null,
  "iv": 25.0,
  "reason": "IV Rank below threshold; waiting for elevated volatility",
  "confidence": "medium",
  "risk_flags": ["low_iv", "unknown_earnings_date"]
}
```

For `SELL` decisions, `strike`, `expiration`, and premium fields are populated. The same JSON object is written to both the decision log and the signal log.

## Project Structure

```
options-agent/
├── config.yaml                           # All configuration (MCP, symbols, scheduling, context limits)
├── src/
│   ├── __init__.py
│   ├── main.py                           # Entry point — scheduler with immediate + periodic runs
│   ├── config.py                         # YAML config loader with env var substitution and validation
│   ├── agent_runner.py                   # Core execution engine — TradingView pre-fetch + per-symbol loop
│   ├── tv_data_fetcher.py                # TradingView pre-fetch module — drives Playwright from Python
│   ├── covered_call_agent.py             # Covered call wrapper
│   ├── cash_secured_put_agent.py         # Cash secured put wrapper
│   ├── open_call_monitor_agent.py        # Open call position monitor wrapper
│   ├── open_put_monitor_agent.py         # Open put position monitor wrapper
│   ├── tv_covered_call_instructions.py   # TradingView covered call instructions (no-tools variant)
│   ├── tv_cash_secured_put_instructions.py # TradingView cash secured put instructions (no-tools variant)
│   ├── tv_open_call_instructions.py      # TradingView open call monitor instructions
│   ├── tv_open_put_instructions.py       # TradingView open put monitor instructions
│   └── logger.py                         # JSONL read/write with per-symbol filtering
├── data/
│   ├── covered_call_symbols.txt          # Symbols for covered call analysis (EXCHANGE-SYMBOL format)
│   ├── cash_secured_put_symbols.txt      # Symbols for cash secured put analysis
│   ├── opened_calls.txt                  # Open call positions to monitor (EXCHANGE-SYMBOL,strike,expiration)
│   └── opened_puts.txt                   # Open put positions to monitor
├── logs/                                 # Created at runtime — JSONL decision + signal logs
├── web/
│   ├── __init__.py
│   ├── app.py                            # FastAPI web dashboard — all routes + JSONL utilities
│   ├── templates/                        # Jinja2 HTML templates (dark trading theme)
│   │   ├── base.html                     # Base layout with nav
│   │   ├── dashboard.html                # Main dashboard — signal overview + activity feed
│   │   ├── signals.html                  # Signal list for agent+symbol
│   │   ├── signal_detail.html            # Single signal + backing decisions
│   │   ├── settings.html                 # Data file editor
│   │   └── chat.html                     # Chat interface
│   └── static/
│       ├── style.css                     # Dark trading theme CSS
│       └── app.js                        # Client-side JS (row clicks, auto-refresh)
├── run_web.py                            # Web dashboard entry point
├── requirements.txt
└── README.md
```

## Deploy to Azure Container Apps

This section deploys the Stock Options Manager to Azure Container Apps with persistent storage via Azure Files. It assumes your Azure AI Foundry project and model deployment already exist.

### 1. Set Environment Variables

```bash
# Azure resource configuration
export RESOURCE_GROUP="rg-stock-options-manager"
export LOCATION="eastus2"
export STORAGE_ACCOUNT="stoptionsmanager"        # must be globally unique, lowercase, no dashes
export CONTAINER_ENV="cae-stock-options-manager"
export CONTAINER_APP="ca-stock-options-manager"

# Azure OpenAI (from your existing Foundry deployment)
export AZURE_AI_PROJECT_ENDPOINT="https://your-project.services.ai.azure.com"
export MODEL_DEPLOYMENT="gpt-5.1"
export AZURE_OPENAI_API_KEY="your-api-key-here"

# Container image (built by GitHub Actions)
export IMAGE="ghcr.io/dsanchor/stock-options-manager:latest"
```

### 2. Create Resource Group

```bash
az group create \
  --name $RESOURCE_GROUP \
  --location $LOCATION
```

### 3. Create Azure Files Storage Account and File Shares

```bash
# Create storage account
az storage account create \
  --name $STORAGE_ACCOUNT \
  --resource-group $RESOURCE_GROUP \
  --location $LOCATION \
  --sku Standard_LRS \
  --kind StorageV2

# Get storage account key
export STORAGE_KEY=$(az storage account keys list \
  --account-name $STORAGE_ACCOUNT \
  --resource-group $RESOURCE_GROUP \
  --query "[0].value" -o tsv)

# Create file shares for data and logs
az storage share create --name data --account-name $STORAGE_ACCOUNT --account-key $STORAGE_KEY
az storage share create --name logs --account-name $STORAGE_ACCOUNT --account-key $STORAGE_KEY
```

### 4. Create Container Apps Environment with Storage Mounts

```bash
# Create the Container Apps environment
az containerapp env create \
  --name $CONTAINER_ENV \
  --resource-group $RESOURCE_GROUP \
  --location $LOCATION

# Add Azure Files storage to the environment
az containerapp env storage set \
  --name $CONTAINER_ENV \
  --resource-group $RESOURCE_GROUP \
  --storage-name optionsdata \
  --azure-file-account-name $STORAGE_ACCOUNT \
  --azure-file-account-key $STORAGE_KEY \
  --azure-file-share-name data \
  --access-mode ReadWrite

az containerapp env storage set \
  --name $CONTAINER_ENV \
  --resource-group $RESOURCE_GROUP \
  --storage-name optionslogs \
  --azure-file-account-name $STORAGE_ACCOUNT \
  --azure-file-account-key $STORAGE_KEY \
  --azure-file-share-name logs \
  --access-mode ReadWrite
```

### 5. Deploy the Container App

```bash
az containerapp create \
  --name $CONTAINER_APP \
  --resource-group $RESOURCE_GROUP \
  --environment $CONTAINER_ENV \
  --image $IMAGE \
  --target-port 8000 \
  --ingress external \
  --min-replicas 1 \
  --max-replicas 1 \
  --cpu 2 \
  --memory 4Gi \
  --env-vars \
    AZURE_AI_PROJECT_ENDPOINT="$AZURE_AI_PROJECT_ENDPOINT" \
    MODEL_DEPLOYMENT="$MODEL_DEPLOYMENT" \
    AZURE_OPENAI_API_KEY="$AZURE_OPENAI_API_KEY" \
  --registry-server ghcr.io
```

> **Note:** If your GHCR package is private, add `--registry-username <github-username> --registry-password <github-pat>` with a PAT that has `read:packages` scope.

Now add the volume mounts (requires a YAML update since `az containerapp create` doesn't support volume mounts inline):

```bash
# Export current app config
az containerapp show \
  --name $CONTAINER_APP \
  --resource-group $RESOURCE_GROUP \
  -o yaml > app.yaml
```

Edit `app.yaml` to add volumes and volume mounts under the template section:

```yaml
template:
  volumes:
    - name: data-volume
      storageName: optionsdata
      storageType: AzureFile
    - name: logs-volume
      storageName: optionslogs
      storageType: AzureFile
  containers:
    - name: ca-stock-options-manager
      # ... existing properties ...
      volumeMounts:
        - volumeName: data-volume
          mountPath: /app/data
        - volumeName: logs-volume
          mountPath: /app/logs
```

Apply the updated config:

```bash
az containerapp update \
  --name $CONTAINER_APP \
  --resource-group $RESOURCE_GROUP \
  --yaml app.yaml
```

### 6. Verify Deployment

```bash
# Get the app URL
export APP_URL=$(az containerapp show \
  --name $CONTAINER_APP \
  --resource-group $RESOURCE_GROUP \
  --query "properties.configuration.ingress.fqdn" -o tsv)

echo "Dashboard: https://$APP_URL"

# Check logs
az containerapp logs show \
  --name $CONTAINER_APP \
  --resource-group $RESOURCE_GROUP \
  --follow
```

### Updating the Deployment

After pushing new code (triggers the GitHub Actions workflow to build a new image):

```bash
az containerapp update \
  --name $CONTAINER_APP \
  --resource-group $RESOURCE_GROUP \
  --image $IMAGE
```

## Troubleshooting

### "Environment variable AZURE_AI_PROJECT_ENDPOINT not set"
Make sure you've exported the environment variable with your Azure AI Foundry project endpoint.

### MCP Server Launch Errors
- Ensure Node.js is installed and `npx` is available in PATH
- First run may be slow while downloading `@playwright/mcp` and Chromium
- Test manually: `npx @playwright/mcp@latest --help`
- View detailed MCP logs in the agent output

### Authentication Errors
Ensure your `AZURE_OPENAI_API_KEY` environment variable is set correctly. You can get your API key from the Azure Portal under your Azure OpenAI resource.

### Module Import Errors
Make sure you installed the correct SDK: `pip install agent-framework[foundry]` (NOT `azure-ai-agents`)

## Development

The agent instructions are defined in separate files:
- `src/tv_covered_call_instructions.py` — Covered call instructions
- `src/tv_cash_secured_put_instructions.py` — Cash secured put instructions
- `src/tv_open_call_instructions.py` — Open call monitor instructions
- `src/tv_open_put_instructions.py` — Open put monitor instructions

All instructions assume pre-fetched TradingView data — the LLM receives market data as text and performs analysis only (no browser tools).

## Technical Details

### SDK Migration
This project uses the **Microsoft Agent Framework** (`agent-framework` package from https://github.com/microsoft/agent-framework).

Key components:
- `agent_framework.Agent` - Main agent class
- `agent_framework.foundry.FoundryChatClient` - Azure AI Foundry integration

TradingView data is fetched via the Playwright MCP server (`npx @playwright/mcp@latest`). The server is driven from Python (`tv_data_fetcher.py`), not by the LLM. The LLM receives pre-fetched data as text and performs analysis only — no tools are given to the agent.

---

## Acknowledgments

This project was built with [GitHub Copilot](https://github.com/features/copilot) and [Squad](https://github.com/bradygaster/squad) by [@bradygaster](https://github.com/bradygaster) — an AI team orchestration framework that runs inside Copilot CLI. Squad coordinated multiple specialized agents to develop, test, and iterate on this codebase.
