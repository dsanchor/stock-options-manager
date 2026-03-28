# Stock Options Manager

Periodic options trading analysis using Microsoft Agent Framework with Playwright-based data fetching. All data — watchlists, positions, decisions, and signals — is stored in **Azure CosmosDB** (NoSQL) with a symbol-centric partition model.

## Architecture

Four specialized agents handle options trading:
- **Covered Call Agent**: Analyzes stocks for covered call writing opportunities
- **Cash Secured Put Agent**: Analyzes stocks for cash secured put opportunities
- **Open Call Monitor**: Monitors open covered call positions for assignment risk
- **Open Put Monitor**: Monitors open cash-secured put positions for assignment risk

The first two agents (sell-side) decide whether to **open** new positions. The last two (position monitors) decide whether to **hold or adjust** existing positions.

Both sell-side agents use the Microsoft Agent Framework (`agent-framework`) with TradingView as the data source. Market data is pre-fetched deterministically via [Playwright](https://playwright.dev/python/) (headless Chromium) and passed to the LLM for analysis — the LLM never touches the browser directly.

**Storage backend:** Azure CosmosDB with a single `symbols` container. Each symbol is a partition key containing three document types: `symbol_config` (watchlist flags + positions), `decision` (full audit trail), and `signal` (actionable alerts). See the [Azure CosmosDB Setup](#azure-cosmosdb-setup) section for provisioning.

## How It Works

End-to-end flow for each scheduled run:

```
Scheduler (main.py)
  │
  ├─ Query CosmosDB for symbols with watchlist.covered_call = true
  │    for each symbol:
  │      1. Load per-symbol context (recent decisions + signals from CosmosDB)
  │      2. Pre-fetch TradingView data (overview, technicals, forecast, options chain)
  │      3. LLM analyzes pre-fetched data → structured JSON decision
  │      4. Write decision to CosmosDB; if SELL → also write signal document
  │
  ├─ Query CosmosDB for symbols with watchlist.cash_secured_put = true
  │    (same loop, different agent instructions)
  │
  ├─ Query CosmosDB for symbols with active call positions
  │    for each position:
  │      1. Load position details from symbol_config
  │      2. Pre-fetch TradingView data
  │      3. LLM assesses assignment risk → WAIT or ROLL decision
  │      4. Write decision to CosmosDB; if ROLL/CLOSE → also write signal
  │
  └─ Query CosmosDB for symbols with active put positions
       (same loop, different agent instructions)
```

**Data gathering:** Python pre-fetches ALL TradingView data deterministically — overview, technicals, forecast, and options chain — using the Playwright Python package driven from `tv_data_fetcher.py`. The LLM never touches the browser. It receives the data as text and only performs analysis. See [Pre-fetch Architecture](#pre-fetch-architecture-tradingview) below.

**Per-symbol context injection:** Before each symbol is analyzed, the runner reads that symbol's recent decisions from CosmosDB and injects them into the prompt. Each decision includes whether it triggered a signal (via the `is_signal` field). The LLM sees only context for the symbol it's currently analyzing — not a mix of all symbols. Context depth is configurable in `config.yaml` (`context.max_decision_entries`, default 2, range 0–5).

**Output:** Every symbol produces a decision (SELL, WAIT, or HOLD) written to CosmosDB as a `decision` document. Only SELL decisions also produce a `signal` document — the actionable alerts that the dashboard and downstream systems watch. Position monitors produce WAIT or ROLL decisions, with ROLL/CLOSE decisions creating signal documents.

## Key Concepts

### Decision vs Signal

**Sell-side agents (Covered Call, Cash Secured Put):**
A **decision** is recorded for EVERY symbol on EVERY run as a `decision` document in CosmosDB. Possible values: `SELL`, `WAIT`, or `HOLD`. The decision collection is the complete audit trail. A **signal** is the subset of decisions where the action is `SELL` — stored as a separate `signal` document for efficient querying.

**Position monitors (Open Call Monitor, Open Put Monitor):**
A **decision** is recorded for EVERY position on EVERY run. Possible values: `WAIT`, `ROLL_UP`, `ROLL_DOWN`, `ROLL_OUT`, `ROLL_UP_AND_OUT`, `ROLL_DOWN_AND_OUT`, or `CLOSE`. A **signal** is any decision that is NOT `WAIT` — any roll or close action that requires attention. Positions are stored within the symbol's `symbol_config` document in CosmosDB.

### Open Position Monitors

The Open Call Monitor and Open Put Monitor watch **existing** short options positions for assignment risk. They differ from the sell-side agents in several ways:

| | Sell-Side Agents | Position Monitors |
|---|---|---|
| **Input** | Symbols with watchlist flag enabled in CosmosDB | Symbols with active positions in CosmosDB |
| **Decisions** | SELL / WAIT | WAIT / ROLL_UP / ROLL_DOWN / ROLL_OUT / ROLL_UP_AND_OUT / ROLL_DOWN_AND_OUT / CLOSE |
| **Signals** | SELL only | Any ROLL or CLOSE |
| **Focus** | "Should I open a new position?" | "Is my existing position safe?" |

Positions are managed via the web dashboard or API. Each position is stored within the symbol's `symbol_config` document in CosmosDB with type (call/put), strike, expiration, status, and notes. Position monitors only run for symbols with `status: "active"` positions.

**Profit optimization:** When ALL market indicators unanimously show the position is deeply OTM with no risk catalysts, the monitor may recommend tightening the strike to collect additional premium (ROLL_DOWN for calls, ROLL_UP for puts). This requires unanimous indicator agreement across 9 conditions — conservative by design. Profit-optimization rolls are tagged with a `"profit_optimization"` risk flag to distinguish them from defensive rolls.

**Roll types:**
- **ROLL_UP** — Higher strike, same expiration (gives more room above for calls)
- **ROLL_DOWN** — Lower strike, same expiration (gives more room below for puts)
- **ROLL_OUT** — Same strike, later expiration (more time value)
- **ROLL_UP_AND_OUT** / **ROLL_DOWN_AND_OUT** — Combined strike + expiration adjustment
- **CLOSE** — Buy back without re-selling (exit the position entirely)

### Pre-fetch Architecture (TradingView)

LLMs don't reliably make multi-step browser tool calls. When given Playwright tools directly, they skip pages, fabricate navigation errors, and ignore sequencing instructions.

The solution: `TradingViewFetcher` (`src/tv_data_fetcher.py`) drives Playwright's headless Chromium directly from Python — deterministically, with no LLM involvement. It fetches four pages per symbol:

| Page | Method | Typical Size | Content |
|------|--------|-------------|---------|
| Overview | `page.goto` + `innerText` | ~variable | Current price, market cap, P/E ratio, dividend yield, 52-week range, volume, sector, industry, earnings date |
| Technicals | `page.goto` + `innerText` | ~3K chars | RSI, MACD, Stochastic, all MAs (10-200), pivot points (R1-R3, S1-S3) with Buy/Sell/Neutral signals |
| Forecast | `page.goto` + `innerText` | ~2.5K chars | Analyst consensus, price targets, EPS history, revenue data |
| Options chain | `page.goto` + `click` + `innerText` | ~65K chars | Full chain expanded to best 30-45 DTE expiration |

The agent is created with **no tools** — it only analyzes the pre-fetched data included in its prompt. This is the key pattern: move deterministic multi-step workflows to the host language; let the LLM do what it's good at — analysis.

### Per-symbol Context Filtering

Each symbol's analysis sees its last N decisions (default 2, configurable 0–5). Each decision includes whether it triggered a signal via the `is_signal` field — there is no separate signal configuration. The context provider queries CosmosDB within the symbol's partition, returning only matching entries up to the configured limit. This prevents cross-contamination between symbols and keeps context focused.

Configurable in `config.yaml`:
```yaml
context:
  max_decision_entries: 2   # Recent decisions to inject as agent context (0=none, max 5). Each decision includes its signal status.
  decision_ttl_days: 90
```

### CosmosDB Document Model

All data is stored in Azure CosmosDB using three document types within a single `symbols` container, partitioned by `/symbol`:

| Document Type | Purpose | Growth |
|---|---|---|
| `symbol_config` | One per symbol — watchlist flags, positions, metadata | Static (updated, not appended) |
| `decision` | One per symbol per agent run — full analysis output | ~20/day per symbol |
| `signal` | One per actionable decision (SELL, ROLL, CLOSE) | ~1-5/week per symbol |

Decisions older than 90 days can be configured for TTL-based cleanup. Signals are kept indefinitely for audit.

## Prerequisites

1. **Python 3.12+**
2. **Azure AI Foundry Project** with access to a model deployment (e.g. `gpt-5.1`, `gpt-5.4-mini`)
3. **Azure OpenAI API Key** - Get your API key from Azure Portal
4. **Azure CosmosDB Account** - See [Azure CosmosDB Setup](#azure-cosmosdb-setup) below

## Setup

### 1. Create Virtual Environment and Install Dependencies

```bash
python -m venv venv
source venv/bin/activate 
pip install -r requirements.txt
playwright install chromium
```

This installs:
- `agent-framework[foundry]` - Microsoft Agent Framework with Foundry support
- `playwright` - Headless Chromium for TradingView data fetching
- `pyyaml`, `croniter`, `python-dotenv` - Configuration and scheduling

### 2. Configure Environment Variables

Set your Azure AI Project and CosmosDB credentials:

```bash
# Azure AI / OpenAI
export AZURE_AI_PROJECT_ENDPOINT="https://your-project.services.ai.azure.com"
export MODEL_DEPLOYMENT="gpt-5.1"  # or "gpt-5.4-mini"
export AZURE_OPENAI_API_KEY="your-api-key-here"

# CosmosDB (from provisioning script output or Azure Portal)
export COSMOSDB_ENDPOINT="https://your-account.documents.azure.com:443/"
export COSMOSDB_KEY="your-primary-key"

# No API key needed for TradingView — data is free via Playwright browser automation
```

### 3. Set Up Azure CosmosDB

See the [Azure CosmosDB Setup](#azure-cosmosdb-setup) section below for provisioning instructions.

### 4. Configure Symbols

Symbols and positions are managed via the **web dashboard** or the CosmosDB API. Each symbol has:
- **Watchlist flags**: `covered_call` and `cash_secured_put` (true/false)
- **Positions**: Open call/put positions with strike, expiration, and status

The exchange prefix is used to construct TradingView URLs (e.g., `NYSE` + `MO` → `https://www.tradingview.com/symbols/NYSE-MO/`).

### 5. Adjust Configuration (Optional)

Edit `config.yaml` to customize:

```yaml
azure:
  project_endpoint: "${AZURE_AI_PROJECT_ENDPOINT}"
  model_deployment: "${MODEL_DEPLOYMENT}"  # From env variable (e.g. gpt-5.1, gpt-5.4-mini)
  api_key: "${AZURE_OPENAI_API_KEY}"

cosmosdb:
  endpoint: "${COSMOSDB_ENDPOINT}"
  key: "${COSMOSDB_KEY}"
  database: "stock-options-manager"

context:
  max_decision_entries: 2               # Recent decisions injected per symbol (0=none, max 5). Each includes signal status.
  decision_ttl_days: 90                 # Auto-cleanup old decisions

scheduler:
  cron: "0 9-16/2 * * 1-5"               # Cron expression (e.g. every 2h, Mon-Fri 9am-4pm)
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

Build the image (pre-installs Playwright + Chromium — no Node.js needed):

```bash
docker build -t stock-options-manager .
```

Run with CosmosDB credentials:

```bash
docker run -d --name stock-options-manager \
  -p 8000:8000 \
  -e AZURE_AI_PROJECT_ENDPOINT="https://your-project.services.ai.azure.com" \
  -e MODEL_DEPLOYMENT="gpt-5.1" \
  -e AZURE_OPENAI_API_KEY="your-api-key-here" \
  -e COSMOSDB_ENDPOINT="https://your-account.documents.azure.com:443/" \
  -e COSMOSDB_KEY="your-primary-key" \
  stock-options-manager
```

| Variable | Purpose |
|---|---|
| `AZURE_AI_PROJECT_ENDPOINT` | Azure AI Foundry project endpoint |
| `MODEL_DEPLOYMENT` | Model name (e.g. `gpt-5.1`, `gpt-5.4-mini`) |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API key for authentication |
| `COSMOSDB_ENDPOINT` | CosmosDB account endpoint |
| `COSMOSDB_KEY` | CosmosDB primary key |

View logs:

```bash
docker logs -f stock-options-manager
```

Pass flags (e.g. web-only mode):

```bash
docker run -d --name stock-options-manager-web \
  -p 8000:8000 \
  -e AZURE_AI_PROJECT_ENDPOINT="..." \
  -e MODEL_DEPLOYMENT="gpt-5.1" \
  -e AZURE_OPENAI_API_KEY="your-api-key-here" \
  -e COSMOSDB_ENDPOINT="..." \
  -e COSMOSDB_KEY="..." \
  stock-options-manager --web-only
```

**Pages:**
- **Dashboard** (`/`) — Signals overview by agent type with time-range counts, scheduler status, recent activity feed, and position summary. Auto-refresh toggle (60s).
- **Signal Details** (`/signals/{agent}/{symbol}`) — All signals for a specific symbol, newest first, with decision badges and risk flags.
- **Signal + Decisions** (`/signals/{agent}/{symbol}/{index}`) — Full signal JSON and backing decisions from the same time window.
- **Settings** (`/settings`) — Edit watchlist and position files directly. Changes take effect on the next scheduler tick (data files are re-read on every cron run).
- **Chat** (`/chat`) — Ask questions about your portfolio. Uses the same Azure OpenAI model with recent decisions as context.

## Output

All decisions and signals are stored in Azure CosmosDB. The web dashboard provides a UI for browsing them, or query directly via the CosmosDB Data Explorer.

### Decision Documents (complete audit trail)

Every agent run creates a `decision` document per symbol in CosmosDB. Query by `doc_type = "decision"` and filter by `agent_type` or `symbol`.

### Signal Documents (actionable alerts only)

Actionable decisions (SELL, ROLL, CLOSE) also create a `signal` document linked to the decision. Query by `doc_type = "signal"` for the dashboard's primary read path.

### Example Decision Object

Each decision document in CosmosDB:

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

For `SELL` decisions, `strike`, `expiration`, and premium fields are populated. A corresponding `signal` document is also created with the actionable subset of the decision data.

## Project Structure

```
stock-options-manager/
├── config.yaml                           # All configuration (CosmosDB, scheduling, context limits)
├── src/
│   ├── __init__.py
│   ├── main.py                           # Entry point — scheduler with immediate + periodic runs
│   ├── config.py                         # YAML config loader with env var substitution and validation
│   ├── cosmos_db.py                      # CosmosDB service layer — all database operations
│   ├── context.py                        # Context injection adapter — formats CosmosDB data for prompts
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
├── scripts/
│   └── provision_cosmosdb.sh             # Azure CosmosDB provisioning via az CLI
├── web/
│   ├── __init__.py
│   ├── app.py                            # FastAPI web dashboard — all routes + CosmosDB queries
│   ├── templates/                        # Jinja2 HTML templates (dark trading theme)
│   │   ├── base.html                     # Base layout with nav
│   │   ├── dashboard.html                # Main dashboard — signal overview + activity feed
│   │   ├── signals.html                  # Signal list for agent+symbol
│   │   ├── signal_detail.html            # Single signal + backing decisions
│   │   ├── settings.html                 # Settings (cron expression)
│   │   └── chat.html                     # Chat interface
│   └── static/
│       ├── style.css                     # Dark trading theme CSS
│       └── app.js                        # Client-side JS (row clicks, auto-refresh)
├── run_web.py                            # Web dashboard entry point
├── requirements.txt
└── README.md
```

## Deploy to Azure Container Apps

This section deploys the Stock Options Manager to Azure Container Apps. It assumes your Microsoft Foundry project, model deployment, and CosmosDB account already exist.

### 1. Set Environment Variables

```bash
# Azure resource configuration
export RESOURCE_GROUP="rg-stock-options-manager"
export LOCATION="swedencentral"
export CONTAINER_ENV="cae-stock-options-manager"
export CONTAINER_APP="ca-stock-options-manager"

# Azure OpenAI (from your existing Foundry deployment)
export AZURE_AI_PROJECT_ENDPOINT="https://your-project.services.ai.azure.com"
export MODEL_DEPLOYMENT="gpt-5.1"
export AZURE_OPENAI_API_KEY="your-api-key-here"

# CosmosDB (from provisioning script output)
export COSMOSDB_ENDPOINT="https://your-account.documents.azure.com:443/"
export COSMOSDB_KEY="your-primary-key"

# Container image (built by GitHub Actions)
export IMAGE="ghcr.io/dsanchor/stock-options-manager:latest"
```

### 2. Create Resource Group

```bash
az group create \
  --name $RESOURCE_GROUP \
  --location $LOCATION
```

### 3. Create Container Apps Environment

```bash
# Create the Container Apps environment
az containerapp env create \
  --name $CONTAINER_ENV \
  --resource-group $RESOURCE_GROUP \
  --location $LOCATION
```

### 4. Deploy the Container App

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
  --cpu 1 \
  --memory 2Gi \
  --env-vars \
    AZURE_AI_PROJECT_ENDPOINT="$AZURE_AI_PROJECT_ENDPOINT" \
    MODEL_DEPLOYMENT="$MODEL_DEPLOYMENT" \
    AZURE_OPENAI_API_KEY="$AZURE_OPENAI_API_KEY" \
    COSMOSDB_ENDPOINT="$COSMOSDB_ENDPOINT" \
    COSMOSDB_KEY="$COSMOSDB_KEY"
```

> **Note:** If your GHCR package is private, add `--registry-username <github-username> --registry-password <github-pat>` with a PAT that has `read:packages` scope.

### 5. Verify Deployment

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

## Azure CosmosDB Setup

The application requires an Azure CosmosDB account with a `symbols` container. You can provision it automatically or set it up manually.

### Option A: Automated Provisioning (Recommended)

**Prerequisites:**
- [Azure CLI](https://docs.microsoft.com/en-us/cli/azure/install-azure-cli) installed
- Logged in: `az login`
- Sufficient permissions to create resources

**Run the provisioning script:**

```bash
bash scripts/provision_cosmosdb.sh
```

This creates:
1. Resource group `rg-stock-options-manager`
2. CosmosDB account (serverless) `cosmos-stock-options`
3. Database `stock-options-manager`
4. Container `symbols` with partition key `/symbol`
5. Custom indexing policy optimized for query patterns

The script outputs the `COSMOSDB_ENDPOINT` and `COSMOSDB_KEY` values. Set them as environment variables:

```bash
export COSMOSDB_ENDPOINT="https://cosmos-stock-options.documents.azure.com:443/"
export COSMOSDB_KEY="<primary-key-from-script-output>"
```

**Customizing resource names:** Override defaults with environment variables before running:

```bash
export RESOURCE_GROUP="my-rg"
export LOCATION="westus2"
export COSMOSDB_ACCOUNT="my-cosmos-account"
bash scripts/provision_cosmosdb.sh
```

### Inline az CLI Commands

If you prefer to run each step individually (or want to see exactly what the script does), here are the commands:

> **Note:** Serverless mode is recommended for development and low-traffic workloads — you pay only per request with no minimum cost. It's the cheapest option for this use case.

```bash
# ── Variables ────────────────────────────────────────────────────────────────
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-stock-options-manager}"
LOCATION="${LOCATION:-eastus}"
COSMOSDB_ACCOUNT="${COSMOSDB_ACCOUNT:-cosmos-stock-options}"
DATABASE_NAME="${DATABASE_NAME:-stock-options-manager}"
CONTAINER_NAME="${CONTAINER_NAME:-symbols}"

# ── 1. Create resource group ────────────────────────────────────────────────
az group create \
  --name "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  -o none

# ── 2. Create CosmosDB account (serverless — pay-per-request) ───────────────
az cosmosdb create \
  --name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --kind GlobalDocumentDB \
  --capabilities EnableServerless \
  --default-consistency-level Session \
  --locations regionName="$LOCATION" failoverPriority=0 isZoneRedundant=false \
  -o none

# ── 3. Create database ──────────────────────────────────────────────────────
az cosmosdb sql database create \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --name "$DATABASE_NAME" \
  -o none

# ── 4. Create container with partition key /symbol ──────────────────────────
az cosmosdb sql container create \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --database-name "$DATABASE_NAME" \
  --name "$CONTAINER_NAME" \
  --partition-key-path "/symbol" \
  --partition-key-version 2 \
  -o none

# ── 5. Apply custom indexing policy ─────────────────────────────────────────
#   Index query fields: symbol, doc_type, timestamp, watchlist flags, agent_type, decision
#   Exclude large blobs: reason, raw_response, analysis_context
az cosmosdb sql container update \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --database-name "$DATABASE_NAME" \
  --name "$CONTAINER_NAME" \
  --idx '{
    "indexingMode": "consistent",
    "automatic": true,
    "includedPaths": [
      {"path": "/symbol/?"},
      {"path": "/doc_type/?"},
      {"path": "/timestamp/?"},
      {"path": "/watchlist/covered_call/?"},
      {"path": "/watchlist/cash_secured_put/?"},
      {"path": "/agent_type/?"},
      {"path": "/decision/?"}
    ],
    "excludedPaths": [
      {"path": "/reason/*"},
      {"path": "/raw_response/*"},
      {"path": "/analysis_context/*"},
      {"path": "/*"}
    ]
  }' \
  -o none

# ── 6. Retrieve endpoint and key ────────────────────────────────────────────
az cosmosdb show \
  --name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --query documentEndpoint \
  --output tsv

az cosmosdb keys list \
  --name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --query primaryMasterKey \
  --output tsv
```

### Option B: Manual Setup via Azure Portal

1. Go to **Azure Portal** → **Create a resource** → **Azure Cosmos DB** → **NoSQL**
2. Create account with **serverless** capacity mode
3. Create database: `stock-options-manager`
4. Create container: `symbols` with partition key `/symbol`
5. Go to **Keys** → copy the **URI** (endpoint) and **PRIMARY KEY**
6. Set environment variables as shown above

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `AZURE_AI_PROJECT_ENDPOINT` | Yes | Azure AI Foundry project endpoint |
| `MODEL_DEPLOYMENT` | Yes | Model deployment name (e.g., `gpt-5.1`, `gpt-5.4-mini`) |
| `AZURE_OPENAI_API_KEY` | Yes | Azure OpenAI API key |
| `COSMOSDB_ENDPOINT` | Yes | CosmosDB account endpoint (e.g., `https://account.documents.azure.com:443/`) |
| `COSMOSDB_KEY` | Yes | CosmosDB primary key |

## Troubleshooting

### "Environment variable AZURE_AI_PROJECT_ENDPOINT not set"
Make sure you've exported the environment variable with your Azure AI Foundry project endpoint.

### CosmosDB Connection Errors
- Verify `COSMOSDB_ENDPOINT` and `COSMOSDB_KEY` are set correctly
- Ensure the CosmosDB account, database (`stock-options-manager`), and container (`symbols`) exist
- Run `bash scripts/provision_cosmosdb.sh` to create missing resources

### Playwright / Chromium Issues
- Ensure Chromium is installed: `playwright install chromium`
- First run may be slow while downloading Chromium
- In Docker, Chromium is pre-installed during image build

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

TradingView data is fetched via the Python Playwright package using headless Chromium. The browser is driven from Python (`tv_data_fetcher.py`), not by the LLM. The LLM receives pre-fetched data as text and performs analysis only — no tools are given to the agent.

---

## Acknowledgments

This project was built with [GitHub Copilot](https://github.com/features/copilot) and [Squad](https://github.com/bradygaster/squad) by [@bradygaster](https://github.com/bradygaster) — an AI team orchestration framework that runs inside Copilot CLI. Squad coordinated multiple specialized agents to develop, test, and iterate on this codebase.
