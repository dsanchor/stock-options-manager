# Options Trading Agent

Periodic options trading analysis using Microsoft Agent Framework with MCP integration.

## Architecture

Four specialized agents handle options trading:
- **Covered Call Agent**: Analyzes stocks for covered call writing opportunities
- **Cash Secured Put Agent**: Analyzes stocks for cash secured put opportunities
- **Open Call Monitor**: Monitors open covered call positions for assignment risk
- **Open Put Monitor**: Monitors open cash-secured put positions for assignment risk

The first two agents (sell-side) decide whether to **open** new positions. The last two (position monitors) decide whether to **hold or adjust** existing positions.

Both sell-side agents use the Microsoft Agent Framework (`agent-framework`) with MCP (Model Context Protocol) integration to access real-time market data and options pricing. Four data providers are supported — switch between them in `config.yaml`:

| Provider | MCP Server | Transport | Key Features |
|----------|-----------|-----------|--------------|
| **Massive.com** (`massive`) | `mcp_massive` | Local stdio | SQL querying, built-in Black-Scholes Greeks, composable API (search → call → query) |
| **Alpha Vantage** (`alphavantage`) | `mcp.alphavantage.co` | Streamable HTTP | 50+ tools, built-in technical indicators (RSI, BBANDS, MACD), progressive tool discovery, news sentiment scores |
| **Yahoo Finance** (`yahoo`) | `mcp-yahoo-finance` | Local stdio | **Free (no API key)**, 12 direct tools, full options chains with IV, earnings dates, analyst recommendations |
| **TradingView** (`tradingview`) | `@playwright/mcp` | Local stdio | **Free (no API key)**, full JS rendering via headless browser, pre-calculated technical signals (Buy/Sell/Neutral), pivot points (R1-R3, S1-S3), complete options chains, analyst forecasts |

## How It Works

End-to-end flow for each scheduled run:

```
Scheduler (main.py)
  │
  ├─ Covered Call Agent
  │    for each symbol in data/covered_call_symbols.txt:
  │      1. Load per-symbol context (past decisions + signals)
  │      2. Gather market data (provider-dependent — see below)
  │      3. LLM analyzes data → structured JSON decision
  │      4. Log decision to JSONL; if SELL → also log to signal file
  │
  ├─ Cash Secured Put Agent
  │    (same loop, different symbols file + instructions)
  │
  ├─ Open Call Monitor (TradingView only)
  │    for each position in data/opened_calls.txt:
  │      1. Parse position (symbol, strike, expiration)
  │      2. Pre-fetch TradingView data
  │      3. LLM assesses assignment risk → WAIT or ROLL decision
  │      4. Log decision; if ROLL/CLOSE → also log to signal file
  │
  └─ Open Put Monitor (TradingView only)
       (same loop, different positions file + instructions)
```

**Data gathering differs by provider:**

- **TradingView (pre-fetch architecture):** Python pre-fetches ALL data deterministically — overview, technicals, forecast, and options chain — using the Playwright MCP server driven from `tv_data_fetcher.py`. The LLM never touches the browser. It receives the data as text and only performs analysis. See [Pre-fetch Architecture](#pre-fetch-architecture-tradingview) below.
- **All other providers (Massive, Alpha Vantage, Yahoo):** The LLM receives MCP tools directly and makes its own tool calls to gather data before analyzing.

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
| **Providers** | All 4 (Massive, Alpha Vantage, Yahoo, TradingView) | TradingView only |
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
2. **Azure AI Foundry Project** with access to `gpt-5.4-mini` model
3. **Azure Authentication** - Ensure you're logged in via Azure CLI:
   ```bash
   az login
   ```
4. **[Astral UV](https://docs.astral.sh/uv/getting-started/installation/)** (v0.4.0+) - For installing the Massive.com MCP server
5. **[Node.js](https://nodejs.org/)** (v18+) - Required for the TradingView provider if using `npx` instead of Docker/Podman
6. **[Docker](https://www.docker.com/) or [Podman](https://podman.io/)** - Required for the TradingView provider (runs Playwright MCP in a container with bundled Chromium)
5. **API Key** for your chosen data provider (not needed for Yahoo Finance or TradingView):
   - **Massive.com** — [Get one free at massive.com](https://massive.com/?utm_campaign=mcp&utm_medium=referral&utm_source=github)
   - **Alpha Vantage** — [Get a free key at alphavantage.co](https://www.alphavantage.co/support/#api-key)
   - **Yahoo Finance** — No API key needed (free, uses yfinance)
   - **TradingView** — No API key needed (free, uses Playwright headless browser)

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
- `pyyaml`, `croniter`, `python-dotenv` - Configuration and scheduling

### 2. Install the MCP Financial Data Server

**Massive.com** (local stdio — requires pre-install):
```bash
uv tool install "mcp_massive @ git+https://github.com/massive-com/mcp_massive@v0.8.7"
```
> **Note:** If your system Python lacks sqlite3 support, install with a uv-managed Python:
> `uv tool install --python 3.13 "mcp_massive @ git+https://github.com/massive-com/mcp_massive@v0.8.7"`

**Alpha Vantage** (remote MCP server — no local install needed):
Alpha Vantage uses a hosted MCP server at `mcp.alphavantage.co`. No local dependencies are required — just set your `ALPHAVANTAGE_API_KEY` and select `alphavantage` in `config.yaml`.

**Yahoo Finance** (local stdio — no API key needed):
```bash
# No pre-install required — uvx downloads it on first run.
# To verify it works:
uvx mcp-yahoo-finance --help
```
> Yahoo Finance is the easiest provider to get started with — no API key, no account signup. It uses [yfinance](https://github.com/ranaroussi/yfinance) under the hood.

**TradingView** (container-based — no API key needed, requires Docker or Podman):
```bash
# Pull the Playwright MCP container image (includes Chromium + all system deps):
docker pull mcr.microsoft.com/playwright/mcp
# Or with Podman:
podman pull mcr.microsoft.com/playwright/mcp

# Test it works:
echo '{}' | docker run -i --rm --init mcr.microsoft.com/playwright/mcp
```
> TradingView uses the [Playwright MCP server](https://github.com/microsoft/playwright-mcp) running in a container to automate a headless browser that navigates TradingView pages. The container bundles Chromium with all system dependencies — no local browser install needed. Playwright fully renders JavaScript, so options chains, financials, and all dynamic content are available.
>
> **Config for Podman users:** Change `command` in `config.yaml` from `"docker"` to `"podman"`.

### 3. Configure Environment Variables

Set your Azure AI Project endpoint and the API key for your selected provider:

```bash
export AZURE_AI_PROJECT_ENDPOINT="https://your-project.services.ai.azure.com"

# For Massive.com provider:
export MASSIVE_API_KEY="your-massive-api-key"

# For Alpha Vantage provider:
export ALPHAVANTAGE_API_KEY="your-alphavantage-api-key"

# For Yahoo Finance provider:
# No API key needed!

# For TradingView provider:
# No API key needed!
```

> You only need the API key for the provider you've selected in `config.yaml`. Yahoo Finance and TradingView require no key at all.

### 4. MCP Server Configuration

The MCP server is launched automatically as a subprocess when agents run. Switch providers by changing `mcp.provider` in `config.yaml`:

```yaml
mcp:
  provider: "massive"  # Options: "massive", "alphavantage", "yahoo", or "tradingview"
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
  yahoo:
    command: "uvx"
    args: ["mcp-yahoo-finance"]
    description: "Yahoo Finance MCP server (free, no API key)..."
  tradingview:
    command: "podman"
    args: ["run", "-i", "--rm", "--init", "mcr.microsoft.com/playwright/mcp"]
    description: "Playwright MCP for TradingView (free, container with full JS rendering)..."
```

Massive.com uses stdio transport (local subprocess). Alpha Vantage uses streamable HTTP transport (remote server). Yahoo Finance uses stdio transport (local subprocess via `uvx`). TradingView uses stdio transport via a containerized browser (`docker`/`podman run -i`). The transport type is auto-detected from config — stdio providers need `command`+`args`, HTTP providers need `url`. Providers without an `env_key` field (like Yahoo Finance and TradingView) skip API key validation.

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

The exchange prefix is used by the TradingView provider to construct URLs. For other providers, the ticker symbol is automatically extracted (e.g., `NASDAQ-AAPL` → `AAPL`).

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
  model_deployment: "gpt-5.1"          # Model deployment name

mcp:
  provider: "tradingview"               # "massive", "alphavantage", "yahoo", or "tradingview"
  # Per-provider sub-sections (only the selected provider is loaded):
  massive:
    command: "mcp_massive"
    args: []
    env_key: "MASSIVE_API_KEY"
  alphavantage:
    transport: "streamable_http"
    url: "https://mcp.alphavantage.co/mcp?apikey=${ALPHAVANTAGE_API_KEY}"
    env_key: "ALPHAVANTAGE_API_KEY"
  yahoo:
    command: "uvx"
    args: ["mcp-yahoo-finance"]
  tradingview:
    command: "podman"                    # or "docker"
    args: ["run", "-i", "--rm", "--init", "mcr.microsoft.com/playwright/mcp"]

context:
  max_decision_entries: 5               # Recent decisions injected per symbol
  max_signal_entries: 1                 # Recent signals injected per symbol

scheduler:
  cron: "*/30 9-16 * * 1-5"              # Cron expression (e.g. every 30 min, Mon-Fri 9am-4pm)

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

Only the selected provider's section is loaded — environment variables for inactive providers are not required. Providers without `env_key` (Yahoo, TradingView) skip API key validation entirely.

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
├── config.yaml                           # All configuration (provider, symbols, scheduling, context limits)
├── src/
│   ├── __init__.py
│   ├── main.py                           # Entry point — scheduler with immediate + periodic runs
│   ├── config.py                         # YAML config loader with env var substitution and validation
│   ├── agent_runner.py                   # Core execution engine — pre-fetch vs MCP-tool paths, per-symbol loop
│   ├── tv_data_fetcher.py                # TradingView pre-fetch module — drives Playwright from Python
│   ├── covered_call_agent.py             # Covered call wrapper — selects instructions by provider
│   ├── cash_secured_put_agent.py         # Cash secured put wrapper — selects instructions by provider
│   ├── open_call_monitor_agent.py        # Open call position monitor wrapper (TradingView only)
│   ├── open_put_monitor_agent.py         # Open put position monitor wrapper (TradingView only)
│   ├── covered_call_instructions.py      # Massive.com covered call instructions
│   ├── cash_secured_put_instructions.py  # Massive.com cash secured put instructions
│   ├── av_covered_call_instructions.py   # Alpha Vantage covered call instructions
│   ├── av_cash_secured_put_instructions.py # Alpha Vantage cash secured put instructions
│   ├── yf_covered_call_instructions.py   # Yahoo Finance covered call instructions
│   ├── yf_cash_secured_put_instructions.py # Yahoo Finance cash secured put instructions
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
├── requirements.txt
└── README.md
```

## Troubleshooting

### "Environment variable AZURE_AI_PROJECT_ENDPOINT not set"
Make sure you've exported the environment variable with your Azure AI Foundry project endpoint.

### MCP Server Launch Errors
- **Massive.com**: Ensure `mcp_massive` is installed: `uv tool install "mcp_massive @ git+https://github.com/massive-com/mcp_massive@v0.8.7"`
- **Alpha Vantage**: Uses remote server — ensure `ALPHAVANTAGE_API_KEY` is set and you have network connectivity
- **Yahoo Finance**: Uses local subprocess via `uvx` — ensure `uv` is installed. No API key needed. If the first run is slow, `uvx` is downloading the package.
- **TradingView**: Uses the Playwright MCP server in a container via `docker run -i --rm --init mcr.microsoft.com/playwright/mcp` (or `podman`). No API key needed. The container bundles Chromium with all system dependencies. First run may be slow while pulling the image (~500MB). If using Podman instead of Docker, change `command` to `"podman"` in `config.yaml`.
- Verify the correct API key env var is set for your provider (`MASSIVE_API_KEY` or `ALPHAVANTAGE_API_KEY`; Yahoo Finance and TradingView need none)
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
- `src/yf_covered_call_instructions.py` — Covered call instructions (Yahoo Finance)
- `src/yf_cash_secured_put_instructions.py` — Cash secured put instructions (Yahoo Finance)
- `src/tv_covered_call_instructions.py` — Covered call instructions (TradingView)
- `src/tv_cash_secured_put_instructions.py` — Cash secured put instructions (TradingView)
- `src/tv_open_call_instructions.py` — Open call monitor instructions (TradingView only)
- `src/tv_open_put_instructions.py` — Open put monitor instructions (TradingView only)

The trading strategy logic is identical across providers — only the DATA GATHERING PROTOCOL differs to match each MCP server's tool interface.

## Technical Details

### SDK Migration
This project uses the **Microsoft Agent Framework** (`agent-framework` package from https://github.com/microsoft/agent-framework).

Key components:
- `agent_framework.Agent` - Main agent class
- `agent_framework.foundry.FoundryChatClient` - Azure AI Foundry integration
- `agent_framework.MCPStdioTool` - MCP server integration via stdio subprocess
- `agent_framework.MCPStreamableHTTPTool` - MCP server integration via streamable HTTP (remote servers)

Massive.com uses stdio transport (local subprocess). Alpha Vantage uses streamable HTTP transport (remote server at `mcp.alphavantage.co`). Yahoo Finance uses stdio transport (local subprocess via `uvx mcp-yahoo-finance`, free, no API key). TradingView uses stdio transport (containerized headless browser via Docker/Podman running `mcr.microsoft.com/playwright/mcp`, free, renders TradingView pages with full JavaScript support for complete options chain and technical data). All tool calls are auto-approved (`approval_mode="never_require"`).
