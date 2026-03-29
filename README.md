# Stock Options Manager

Periodic options trading analysis using Microsoft Agent Framework with Playwright-based data fetching. All data — watchlists, positions, activities, and alerts — is stored in **Azure CosmosDB** (NoSQL) with a symbol-centric partition model.

## Architecture

Four specialized agents handle options trading:
- **Covered Call Agent**: Analyzes stocks for covered call writing opportunities
- **Cash Secured Put Agent**: Analyzes stocks for cash secured put opportunities
- **Open Call Monitor**: Monitors open covered call positions for assignment risk
- **Open Put Monitor**: Monitors open cash-secured put positions for assignment risk

The first two agents (sell-side) decide whether to **open** new positions. The last two (position monitors) decide whether to **hold or adjust** existing positions.

Both sell-side agents use the Microsoft Agent Framework (`agent-framework`) with TradingView as the data source. Market data is pre-fetched deterministically via [Playwright](https://playwright.dev/python/) (headless Chromium) and passed to the LLM for analysis — the LLM never touches the browser directly.

**Storage backend:** Azure CosmosDB with two containers: `symbols` (watchlists, positions, activities, alerts) and `telemetry` (runtime performance stats with 30-day TTL). Each symbol is a partition key in the symbols container containing three document types: `symbol_config` (watchlist flags + positions), `activity` (full audit trail), and `alert` (actionable alerts). The telemetry container tracks TradingView fetch durations and agent run times, displayed on the Settings page. See the [Azure CosmosDB Setup](#azure-cosmosdb-setup) section for provisioning.

## How It Works

End-to-end flow for each scheduled run:

```
Scheduler (main.py)
  │
  ├─ Query CosmosDB for symbols with watchlist.covered_call = true
  │    for each symbol:
  │      1. Load per-symbol context (recent activities + alerts from CosmosDB)
  │      2. Pre-fetch TradingView data (overview, technicals, forecast, options chain)
  │      3. LLM analyzes pre-fetched data → structured JSON activity
  │      4. Write activity to CosmosDB; if SELL → also write alert document
  │
  ├─ Query CosmosDB for symbols with watchlist.cash_secured_put = true
  │    (same loop, different agent instructions)
  │
  ├─ Query CosmosDB for symbols with active call positions
  │    for each position:
  │      1. Load position details from symbol_config
  │      2. Pre-fetch TradingView data
  │      3. LLM assesses assignment risk → WAIT or ROLL activity
  │      4. Write activity to CosmosDB; if ROLL/CLOSE → also write alert
  │
  └─ Query CosmosDB for symbols with active put positions
       (same loop, different agent instructions)
```

**Data gathering:** Python pre-fetches ALL TradingView data deterministically — overview (targeted div extraction of 5 specific page sections), technicals, forecast, and options chain (API response interception via TradingView scanner endpoints, with DOM fallback) — using the Playwright Python package driven from `tv_data_fetcher.py`. The LLM never touches the browser. It receives the data as text and only performs analysis. See [Pre-fetch Architecture](#pre-fetch-architecture-tradingview) below.

**Per-symbol context injection:** Before each symbol is analyzed, the runner reads that symbol's recent activities from CosmosDB and injects them into the prompt. Each activity includes whether it triggered an alert (via the `is_alert` field). The LLM sees only context for the symbol it's currently analyzing — not a mix of all symbols. Context depth is configurable in `config.yaml` (`context.max_activity_entries`, default 2, range 0–5).

**Output:** Every symbol produces an activity (SELL, WAIT, or HOLD) written to CosmosDB as a `activity` document. Only SELL activitys also produce a `alert` document — the actionable alerts that the dashboard and downstream systems watch. Position monitors produce WAIT or ROLL activities, with ROLL/CLOSE activities creating alert documents. If Telegram notifications are enabled, a message is sent for each alert (see [Telegram Notifications](#telegram-notifications-optional)).

## Key Concepts

### Activity vs Alert

**Sell-side agents (Covered Call, Cash Secured Put):**
A **activity** is recorded for EVERY symbol on EVERY run as an `activity` document in CosmosDB. Possible values: `SELL`, `WAIT`, or `HOLD`. The activity collection is the complete audit trail. An **alert** is the subset of activities where the action is `SELL` — stored as a separate `alert` document for efficient querying.

**Position monitors (Open Call Monitor, Open Put Monitor):**
A **activity** is recorded for EVERY position on EVERY run. Possible values: `WAIT`, `ROLL_UP`, `ROLL_DOWN`, `ROLL_OUT`, `ROLL_UP_AND_OUT`, `ROLL_DOWN_AND_OUT`, or `CLOSE`. An **alert** is any activity that is NOT `WAIT` — any roll or close action that requires attention. Positions are stored within the symbol's `symbol_config` document in CosmosDB.

### Open Position Monitors

The Open Call Monitor and Open Put Monitor watch **existing** short options positions for assignment risk. They differ from the sell-side agents in several ways:

| | Sell-Side Agents | Position Monitors |
|---|---|---|
| **Input** | Symbols with watchlist flag enabled in CosmosDB | Symbols with active positions in CosmosDB |
| **Activities** | SELL / WAIT | WAIT / ROLL_UP / ROLL_DOWN / ROLL_OUT / ROLL_UP_AND_OUT / ROLL_DOWN_AND_OUT / CLOSE |
| **Alerts** | SELL only | Any ROLL or CLOSE |
| **Focus** | "Should I open a new position?" | "Is my existing position safe?" |

Positions are managed via the web dashboard or API. Each position is stored within the symbol's `symbol_config` document in CosmosDB with type (call/put), strike, expiration, status, and notes. Position monitors only run for symbols with `status: "active"` positions.

**Profit optimization:** When ALL market indicators unanimously show the position is deeply OTM with no risk catalysts, the monitor may recommend tightening the strike to collect additional premium (ROLL_DOWN for calls, ROLL_UP for puts). This requires unanimous indicator agreement across 9 conditions — conservative by design. Profit-optimization rolls are tagged with a `"profit_optimization"` risk flag to distinguish them from defensive rolls.

**Roll types:**
- **ROLL_UP** — Higher strike, same expiration (gives more room above for calls)
- **ROLL_DOWN** — Lower strike, same expiration (gives more room below for puts)
- **ROLL_OUT** — Same strike, later expiration (more time value)
- **ROLL_UP_AND_OUT** / **ROLL_DOWN_AND_OUT** — Combined strike + expiration adjustment
- **CLOSE** — Buy back without re-selling (exit the position entirely)

### Position Lifecycle

**Open Position from Alert:**
When a sell-side agent (covered_call, cash_secured_put) generates a SELL alert, the activity detail page displays an "Open Position" button. Clicking it creates a position from the alert data (strike, expiration, type), storing a `source` snapshot of the original alert for full traceability. The watchlist flag is disabled for that symbol, and related activities/alerts are cascade-deleted.

**Roll Position from Alert:**
When a monitor agent (open_call_monitor, open_put_monitor) generates a ROLL alert, the activity detail page shows a "Roll Position" button. Clicking it atomically closes the old position and creates a new one. The old position is marked `status: "closed"` with a `closing_source` snapshot (the alert) and `rolled_to` pointing to the new position ID. The new position carries a `source` snapshot and `rolled_from` pointing to the old position ID, creating an auditable chain.

**Manual Roll:**
Active positions in the Symbol Detail page have a Roll button in the positions table. Clicking it opens an inline form to specify new strike, new expiration, and optional notes. The same `rolled_to`/`rolled_from` chain is created without alert snapshots.

**Position Actions:**
- **Close** — Marks position as closed (status: "closed") with the timestamp
- **Roll** — Atomically closes current position and opens a new one, maintaining traceability chain
- **Delete** — Permanently removes the position and cascade-deletes all linked activities/alerts

**Position Model Example:**
```json
{
  "position_id": "pos_MO_call_60.0_20250620",
  "type": "call",
  "strike": 60.0,
  "expiration": "2025-06-20",
  "opened_at": "2025-03-15T10:00:00Z",
  "status": "active",
  "notes": "",
  "source": {
    "activity_id": "dec_...",
    "agent_type": "covered_call",
    "timestamp": "2025-03-15T10:00:00Z"
  },
  "rolled_from": "pos_MO_call_55.0_20250520"
}
```

### Pre-fetch Architecture (TradingView)

LLMs don't reliably make multi-step browser tool calls. When given Playwright tools directly, they skip pages, fabricate navigation errors, and ignore sequencing instructions.

The solution: `TradingViewFetcher` (`src/tv_data_fetcher.py`) drives Playwright's headless Chromium directly from Python — deterministically, with no LLM involvement. It fetches four pages per symbol:

| Page | Method | Typical Size | Content |
|------|--------|-------------|---------|
| Overview | `page.goto` + `getElementById` (targeted) | ~variable | Upcoming earnings, key stats, employees, company info, financials overview (5 specific div sections by ID) |
| Technicals | `page.goto` + `innerText` | ~3K chars | RSI, MACD, Stochastic, all MAs (10-200), pivot points (R1-R3, S1-S3) with Buy/Sell/Neutral alerts |
| Forecast | `page.goto` + `innerText` | ~2.5K chars | Analyst consensus, price targets, EPS history, revenue data |
| Options chain | `page.on("response")` interception | ~variable | Structured JSON from TradingView scanner API (`scanner.tradingview.com/global/scan2` + `options/scan2`): strikes, bids, asks, greeks, volume, OI. Falls back to DOM `innerText` if no API responses captured |

The agent is created with **no tools** — it only analyzes the pre-fetched data included in its prompt. This is the key pattern: move deterministic multi-step workflows to the host language; let the LLM do what it's good at — analysis.

### Per-symbol Context Filtering

Each symbol's analysis sees its last N activities (default 2, configurable 0–5). Each activity includes whether it triggered an alert via the `is_alert` field — there is no separate alert configuration. The context provider queries CosmosDB within the symbol's partition, returning only matching entries up to the configured limit. This prevents cross-contamination between symbols and keeps context focused.

Configurable in `config.yaml`:
```yaml
context:
  max_activity_entries: 2   # Recent activities to inject as agent context (0=none, max 5). Each activity includes its alert status.
  activity_ttl_days: 90
```

### CosmosDB Document Model

All data is stored in Azure CosmosDB across two containers:

**`symbols` container** (partition key: `/symbol`) — three document types:

| Document Type | Purpose | Growth |
|---|---|---|
| `symbol_config` | One per symbol — watchlist flags, positions, metadata | Static (updated, not appended) |
| `activity` | One per symbol per agent run — full analysis output | ~20/day per symbol |
| `alert` | One per actionable activity (SELL, ROLL, CLOSE) | ~1-5/week per symbol |

**`telemetry` container** (partition key: `/metric_type`) — runtime performance stats with 30-day TTL:

| Metric Type | Purpose | Fields |
|---|---|---|
| `tv_fetch` | TradingView page fetch timing | resource, duration_seconds, response_size_chars |
| `agent_run` | End-to-end agent execution timing | agent_type, duration_seconds |

Telemetry stats are displayed on the Settings page and auto-expire after 30 days.

Activities older than 90 days can be configured for TTL-based cleanup. Alerts are kept indefinitely for audit.

## Output

All activities and alerts are stored in Azure CosmosDB. The web dashboard provides a UI for browsing them, or query directly via the CosmosDB Data Explorer.

### Activity Documents (complete audit trail)

Every agent run creates an `activity` document per symbol in CosmosDB. Query by `doc_type = "activity"` and filter by `agent_type` or `symbol`.

### Alert Documents (actionable alerts only)

Actionable activities (SELL, ROLL, CLOSE) also create a `alert` document linked to the activity. Query by `doc_type = "alert"` for the dashboard's primary read path.

### Example Activity Object

Each activity document in CosmosDB:
```json
{
  "timestamp": "2026-03-27T00:00:00Z",
  "symbol": "MO",
  "exchange": "NYSE",
  "agent": "covered_call",
  "activity": "WAIT",
  "strike": null,
  "expiration": null,
  "iv": 25.0,
  "reason": "IV Rank below threshold; waiting for elevated volatility",
  "confidence": "medium",
  "risk_flags": ["low_iv", "unknown_earnings_date"]
}
```

For `SELL` activities, `strike`, `expiration`, and premium fields are populated. A corresponding `alert` document is also created with the actionable subset of the activity data.

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
│   └── telegram_notifier.py              # Telegram notification service — sends alerts via bot API
├── scripts/
│   └── provision_cosmosdb.sh             # Azure CosmosDB provisioning via az CLI
├── web/
│   ├── __init__.py
│   ├── app.py                            # FastAPI web dashboard — all routes + CosmosDB queries
│   ├── templates/                        # Jinja2 HTML templates (dark trading theme)
│   │   ├── base.html                     # Base layout with nav
│   │   ├── dashboard.html                # Main dashboard — alert overview + activity feed
│   │   ├── alerts.html                  # Alert list for agent+symbol
│   │   ├── alert_detail.html            # Single alert + backing activities
│   │   ├── settings.html                 # Settings (cron expression)
│   │   ├── symbol_detail.html            # Symbol detail with positions, activities, per-symbol chat
│   │   ├── fetch_preview.html            # Raw data debug/preview page
│   │   └── chat.html                     # Chat interface
│   └── static/
│       ├── style.css                     # Dark trading theme CSS
│       └── app.js                        # Client-side JS (row clicks, trigger buttons)
├── run_web.py                            # Web dashboard entry point
├── requirements.txt
└── README.md
```

## Web Dashboard

- **Dashboard** (`/`) — Alerts overview by agent type with time-range counts, scheduler status, recent activity feed, and position summary.
- **Alert Details** (`/alerts/{agent}/{symbol}`) — All alerts for a specific symbol, newest first, with activity badges and risk flags.
- **Alert + Activities** (`/alerts/{agent}/{symbol}/{index}`) — Full alert JSON and backing activities from the same time window.
- **Symbol Detail** (`/symbols/{symbol}`) — Full detail page for a symbol: expandable positions with source traceability, Close/Roll/Delete actions, activities, alerts, and "Open Position from Alert" / "Roll Position from Alert" buttons on activity detail; per-symbol chat.
- **Fetch Preview** (`/symbols/{symbol}/fetch-preview`) — Debug page showing raw TradingView data for each resource (overview, technicals, forecast, options chain) with fetch timing and size.
- **Chat** (`/chat`) — Ask questions about your portfolio. Uses the same Azure OpenAI model with recent activities as context.
- **Settings** (`/settings`) — Scheduler config, Telegram notifications toggle & test button, runtime stats (today/7d/30d telemetry), and a Debug TradingView Fetch tool for testing data fetching per symbol.

---

## Running Locally

### Prerequisites

1. **Python 3.12+**
2. **Azure AI Foundry Project** with access to a model deployment (e.g. `gpt-5.1`, `gpt-5.4-mini`)
3. **Azure OpenAI API Key** - Get your API key from Azure Portal
4. **Azure CosmosDB Account** - See [Azure CosmosDB Setup](#azure-cosmosdb-setup) below

### Setup

#### 1. Create Virtual Environment and Install Dependencies

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

#### 2. Configure Environment Variables

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

#### 3. (Optional) Set Up Telegram Notifications

Receive alerts directly on Telegram. Skip this section if you don't need notifications.

**Create a Telegram bot:**
1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the prompts (choose a name, then a username)
3. Copy the bot token (format: `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`)

**Get your chat ID:**
1. Add the bot to a group or start a direct message with it
2. Send any message to the bot
3. Visit `https://api.telegram.org/bot<TOKEN>/getUpdates` (replace `<TOKEN>` with your bot token)
4. Look for `chat.id` in the JSON response — copy the ID (group IDs are negative)

**Set environment variables:**
```bash
export TELEGRAM_BOT_TOKEN="123456:ABC-DEF..."
export TELEGRAM_CHAT_ID="-1001234567890"  # Use negative for groups
```

**Enable in config.yaml** (see step 5) or toggle on the Settings page. Use the **Test** button to verify connectivity.

#### 4. Set Up Azure CosmosDB

See the [Azure CosmosDB Setup](#azure-cosmosdb-setup) section below for provisioning instructions.

#### 5. Configure Symbols

Symbols and positions are managed via the **web dashboard** or the CosmosDB API. Each symbol has:
- **Watchlist flags**: `covered_call` and `cash_secured_put` (true/false)
- **Positions**: Open call/put positions with strike, expiration, and status

The exchange prefix is used to construct TradingView URLs (e.g., `NYSE` + `MO` → `https://www.tradingview.com/symbols/NYSE-MO/`).

#### 6. Adjust Configuration (Optional)

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
  max_activity_entries: 2               # Recent activities injected per symbol (0=none, max 5). Each includes alert status.
  activity_ttl_days: 90                 # Auto-cleanup old activities

scheduler:
  cron: "0 9-16/2 * * 1-5"               # Cron expression (e.g. every 2h, Mon-Fri 9am-4pm)

telegram:
  enabled: false                        # Toggle on/off (also controllable from Settings UI)
  bot_token: "${TELEGRAM_BOT_TOKEN}"    # Bot token from @BotFather
  chat_id: "${TELEGRAM_CHAT_ID}"        # Target chat/group/channel ID
```

### Running

#### Full app (web dashboard + scheduler)

```bash
python run.py
```

Opens the dashboard at http://localhost:8000 and starts the agent scheduler in a background thread. Press `Ctrl+C` to stop both.

#### Web dashboard only

```bash
python run.py --web-only
```

#### Scheduler only (no web UI)

```bash
python run.py --scheduler-only
```

#### Options

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

---

## Azure Deployment

### Prerequisites

- [Azure CLI](https://docs.microsoft.com/en-us/cli/azure/install-azure-cli) installed and logged in (`az login`)
- Azure AI Foundry project with a model deployment already exists
- Container image built (e.g., via GitHub Actions)

### 1. Set Variables

```bash
# ── Resource names ───────────────────────────────────────────────────────────
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-stock-options-manager}"
LOCATION="${LOCATION:-eastus}"

# CosmosDB
COSMOSDB_ACCOUNT="${COSMOSDB_ACCOUNT:-cosmos-stock-options}"
DATABASE_NAME="${DATABASE_NAME:-stock-options-manager}"
CONTAINER_NAME="${CONTAINER_NAME:-symbols}"

# Container Apps
CONTAINER_ENV="${CONTAINER_ENV:-cae-stock-options-manager}"
CONTAINER_APP="${CONTAINER_APP:-ca-stock-options-manager}"
IMAGE="${IMAGE:-ghcr.io/dsanchor/stock-options-manager:latest}"

# ── Credentials (fill these in) ─────────────────────────────────────────────
AZURE_AI_PROJECT_ENDPOINT="${AZURE_AI_PROJECT_ENDPOINT:-your-project-endpoint}"
MODEL_DEPLOYMENT="${MODEL_DEPLOYMENT:-gpt-5.1}"
AZURE_OPENAI_API_KEY="${AZURE_OPENAI_API_KEY:-your-api-key-here}"
```

### 2. Create Resource Group

```bash
az group create \
  --name "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  -o none
```

### 3. Provision CosmosDB

Serverless is recommended — pay-per-request with no minimum cost.

```bash
# Create CosmosDB account (serverless)
az cosmosdb create \
  --name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --kind GlobalDocumentDB \
  --capacity-mode Serverless \
  --default-consistency-level Session \
  --locations regionName="$LOCATION" failoverPriority=0 isZoneRedundant=false \
  -o none

# Create database
az cosmosdb sql database create \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --name "$DATABASE_NAME" \
  -o none

# Create container with partition key /symbol
az cosmosdb sql container create \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --database-name "$DATABASE_NAME" \
  --name "$CONTAINER_NAME" \
  --partition-key-path "/symbol" \
  --partition-key-version 2 \
  -o none

# Create telemetry container (partition key /metric_type, per-document TTL enabled)
az cosmosdb sql container create \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --database-name "$DATABASE_NAME" \
  --name "telemetry" \
  --partition-key-path "/metric_type" \
  --partition-key-version 2 \
  -o none

# Then update to enable TTL (30 days = 2592000 seconds)
az cosmosdb sql container update \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --database-name "$DATABASE_NAME" \
  --name "telemetry" \
  --ttl 2592000 \
  -o none

# Apply custom indexing policy
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
      {"path": "/activity/?"}
    ],
    "excludedPaths": [
      {"path": "/reason/*"},
      {"path": "/raw_response/*"},
      {"path": "/analysis_context/*"},
      {"path": "/*"}
    ]
  }' \
  -o none

# Retrieve endpoint and key
COSMOSDB_ENDPOINT=$(az cosmosdb show \
  --name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --query documentEndpoint \
  --output tsv)

COSMOSDB_KEY=$(az cosmosdb keys list \
  --name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --query primaryMasterKey \
  --output tsv)

echo "COSMOSDB_ENDPOINT=$COSMOSDB_ENDPOINT"
echo "COSMOSDB_KEY=$COSMOSDB_KEY"
```

> **Alternatively**, run `bash scripts/provision_cosmosdb.sh` which performs these same steps, or create the resources manually via the [Azure Portal](https://portal.azure.com) (CosmosDB → NoSQL → serverless capacity mode).

### 4. Deploy to Container Apps

```bash
# Create Container Apps environment
az containerapp env create \
  --name "$CONTAINER_ENV" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  -o none

# Deploy the container app
az containerapp create \
  --name "$CONTAINER_APP" \
  --resource-group "$RESOURCE_GROUP" \
  --environment "$CONTAINER_ENV" \
  --image "$IMAGE" \
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
    COSMOSDB_KEY="$COSMOSDB_KEY" \
  -o none
```

> **Note:** If your GHCR package is private, add `--registry-username <github-username> --registry-password <github-pat>` with a PAT that has `read:packages` scope.

```bash
# Verify — get the app URL
APP_URL=$(az containerapp show \
  --name "$CONTAINER_APP" \
  --resource-group "$RESOURCE_GROUP" \
  --query "properties.configuration.ingress.fqdn" -o tsv)

echo "Dashboard: https://$APP_URL"

# Check logs
az containerapp logs show \
  --name "$CONTAINER_APP" \
  --resource-group "$RESOURCE_GROUP" \
  --follow
```

### 5. Update Deployment

After pushing new code (triggers the GitHub Actions workflow to build a new image):

```bash
az containerapp update \
  --name "$CONTAINER_APP" \
  --resource-group "$RESOURCE_GROUP" \
  --image "$IMAGE"
```

---

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
- Ensure the CosmosDB account, database (`stock-options-manager`), and containers (`symbols`, `telemetry`) exist
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

### SDK Information

This project uses the **Microsoft Agent Framework** (`agent-framework` package from https://github.com/microsoft/agent-framework).

Key components:
- `agent_framework.Agent` - Main agent class
- `agent_framework.foundry.FoundryChatClient` - Azure AI Foundry integration

TradingView data is fetched via the Python Playwright package using headless Chromium. The browser is driven from Python (`tv_data_fetcher.py`), not by the LLM. The LLM receives pre-fetched data as text and performs analysis only — no tools are given to the agent.

---

## Acknowledgments

This project was built with [GitHub Copilot](https://github.com/features/copilot) and [Squad](https://github.com/bradygaster/squad) by [@bradygaster](https://github.com/bradygaster) — an AI team orchestration framework that runs inside Copilot CLI. Squad coordinated multiple specialized agents to develop, test, and iterate on this codebase.
