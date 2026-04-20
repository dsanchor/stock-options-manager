# Stock Options Manager

Periodic options trading analysis using Microsoft Agent Framework with hybrid TradingView data fetching — `requests` + `BeautifulSoup` + TradingView scanner API for most data, Playwright (headless Chromium) only for options chain — fronted by an **in-memory cache layer** (`tv_cache.py`) with per-key TTL and async locking to eliminate redundant fetches across agents. All data — watchlists, positions, activities, reports, and alerts — is stored in **Azure CosmosDB** (NoSQL) with a symbol-centric partition model.

## Architecture

Five specialized agents handle options trading:
- **Covered Call Agent**: Analyzes stocks for covered call writing opportunities
- **Cash Secured Put Agent**: Analyzes stocks for cash secured put opportunities
- **Open Call Monitor**: Monitors open covered call positions for assignment risk
- **Open Put Monitor**: Monitors open cash-secured put positions for assignment risk
- **Report Agent**: Generates comprehensive per-symbol reports combining technical analysis, dividends, options chain, open position risk, and monitoring recommendations

The first two agents (sell-side) decide whether to **open** new positions. The next two (position monitors) decide whether to **hold or adjust** existing positions. The report agent provides on-demand deep-dive analysis accessible from each symbol's detail page. Additionally, **per-symbol chat** is available directly from the symbol detail page, offering context-aware conversations with pre-loaded TradingView data via the cache layer.

Both sell-side agents use the Microsoft Agent Framework (`agent-framework`) with TradingView as the data source. Market data is pre-fetched deterministically — overview, technicals, forecast, and dividends via `requests` + `BeautifulSoup` + TradingView scanner API; options chain via [Playwright](https://playwright.dev/python/) (headless Chromium) — and passed to the LLM for analysis. The LLM never touches the browser or makes HTTP requests directly.

**Storage backend:** Azure CosmosDB with three containers: `symbols` (watchlists, positions, activities, alerts, reports), `telemetry` (runtime performance stats with 30-day TTL), and `settings` (application configuration persistence). Each symbol is a partition key in the symbols container containing four document types: `symbol_config` (watchlist flags + positions), `activity` (full audit trail), `alert` (actionable alerts), and `report` (generated symbol reports). The telemetry container tracks TradingView fetch durations and agent run times, displayed on the Settings page. The settings container persists application configuration with partition key `/id`. See the [Azure CosmosDB Setup](#azure-cosmosdb-setup) section for provisioning.

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

**Data gathering:** Python pre-fetches ALL TradingView data deterministically from `tv_data_fetcher.py`. Overview, technicals, forecast, and dividends are fetched via `requests` + `BeautifulSoup` + TradingView scanner API (`scanner.tradingview.com/america/scan2`) — no browser needed. Options chain still uses Playwright (headless Chromium) with `page.on("response")` interception because it requires browser authentication. The LLM never touches the browser or makes HTTP requests. It receives the data as text and only performs analysis. See [Pre-fetch Architecture](#pre-fetch-architecture-tradingview) below.

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

**Profit optimization (premium-first roll policy):** When market indicators show the position is deeply OTM with no risk catalysts, the monitor may recommend tightening the strike to collect additional premium (ROLL_DOWN for calls, ROLL_UP for puts). This requires 3 mandatory conditions (≥10 DTE, deeply OTM, net credit) plus at least 4 of 7 flexible conditions (super-majority gate) — conservative by design. The ultra-defensive roll threshold caps maximum debit at $1 ($100 per contract). Profit-optimization rolls are tagged with a `"profit_optimization"` risk flag to distinguish them from defensive rolls. Monitor agents prioritize premium collection when rolling, considering whether to tighten strikes more aggressively when conditions allow.

**Roll types:**
- **ROLL_UP** — Higher strike, same expiration (gives more room above for calls)
- **ROLL_DOWN** — Lower strike, same expiration (gives more room below for puts)
- **ROLL_OUT** — Same strike, later expiration (more time value)
- **ROLL_UP_AND_OUT** / **ROLL_DOWN_AND_OUT** — Combined strike + expiration adjustment
- **CLOSE** — Buy back without re-selling (exit the position entirely)

### Risk Rating (Sell-Side Agents)

Every sell-side agent output (Covered Call and Cash Secured Put) includes a **risk rating** on a 0–10 scale, quantifying how risky the recommended action is.

**Scoring:** 5 dimensions, each scored 0–2 (sum = 0–10):
- **Covered Call:** Volatility, Assignment, Technical, Calendar, Sentiment
- **Cash Secured Put:** Fundamental, Technical, Volatility, Calendar, Sentiment

**Interpretation:**
| Score | Level | Guidance |
|-------|-------|----------|
| 0–2 | Low | Strong setup, high conviction |
| 3–4 | Moderate | Acceptable with awareness |
| 5–6 | Elevated | Proceed with caution |
| 7–8 | High | Likely should WAIT |
| 9–10 | Very high | Definitely WAIT |

The rating appears in JSON output (`risk_rating` integer + `risk_rating_breakdown` object) and in the SUMMARY line (`Risk X/10`). Telegram sell alerts also include `Risk: X/10`.

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

The solution: `TradingViewFetcher` (`src/tv_data_fetcher.py`) uses a hybrid approach — `requests` + `BeautifulSoup` + TradingView scanner API for most data, Playwright only for options chain. No LLM involvement in any fetching. It gathers five data sets per symbol, backed by the [TradingView Data Cache](#tradingview-data-cache) to avoid redundant fetches when multiple agents or endpoints (chat, report, analysis) request the same symbol data:

| Data | Method | Typical Size | Content |
|------|--------|-------------|---------|
| Overview | `requests` + `BeautifulSoup` (embedded JSON) + scanner API for fundamentals | ~variable | Market cap, P/E, EPS, dividend yield, sector, employees, company description |
| Technicals | `requests` + `BeautifulSoup` (embedded JSON) + scanner API fallback | ~3K chars | Oscillators (RSI, MACD, Stochastic), moving averages (EMA/SMA 10-200), summary recommendations with Buy/Sell/Neutral signals |
| Forecast | `requests` + `BeautifulSoup` (embedded JSON) + scanner API fallback | ~2.5K chars | Analyst consensus, price targets (high/median/low), ratings distribution |
| Dividends | `requests` + `BeautifulSoup` + scanner API | ~variable | Dividend yield, amount, ex-date, payment frequency, payout ratio |
| Options chain | Playwright `page.on("response")` interception | ~variable | Structured JSON from TradingView scanner API (`scanner.tradingview.com/global/scan2` + `options/scan2`): strikes, bids, asks, greeks, volume, OI. Requires browser authentication — scanner API rejects unauthenticated options requests. Falls back to DOM `innerText` if no API responses captured |

Playwright and Chromium are initialized lazily — they only start when options chain is actually fetched, saving resources when only the four HTTP-based fetchers run.

**Anti-403 resilience:** TradingView fetching includes a 4-phase anti-403 strategy for improved reliability — progressive backoff, header rotation, session refresh, and sequential full-analysis mode as a last resort. Error tracking and stats are displayed on the Settings page.

**Fund-type symbol handling:** For fund-type symbols (e.g., `NYSE:O`) where `_extract_pro_symbol()` returns `None`, the fetcher falls back to a `full_symbol.replace("-", ":", 1)` format to construct valid TradingView URLs.

The agent is created with **no tools** — it only analyzes the pre-fetched data included in its prompt. This is the key pattern: move deterministic multi-step workflows to the host language; let the LLM do what it's good at — analysis.

### TradingView Data Cache

An in-memory cache layer (`src/tv_cache.py`) sits between consumers (chat, report, analysis endpoints) and `TradingViewFetcher`, eliminating redundant fetches when multiple agents analyze the same symbol in a short time window.

**How it works:**
- Cache keys are per-symbol per-data-type: `(symbol, data_type)` where `data_type` is one of `overview`, `technicals`, `forecast`, `dividends`, or `options_chain`
- Each entry has a configurable TTL — stale entries are evicted automatically
- Async locking prevents thundering-herd problems: if two agents request the same symbol simultaneously, only one fetch executes; the other awaits the cached result
- The cache is process-local (in-memory) — no external infrastructure required

**Consumers:** The cache is used by the `/chat` endpoint (Portfolio Chat and Quick Analysis), the Report Agent, and the per-symbol analysis runner. Any component that calls `TradingViewFetcher` benefits from deduplication transparently.

### Per-symbol Context Filtering

Each symbol's analysis sees its last N activities (default 2, configurable 0–5). Each activity includes whether it triggered an alert via the `is_alert` field — there is no separate alert configuration. The context provider queries CosmosDB within the symbol's partition, returning only matching entries up to the configured limit. This prevents cross-contamination between symbols and keeps context focused.

Configurable in `config.yaml`:
```yaml
context:
  max_activity_entries: 2   # Recent activities to inject as agent context (0=none, max 5). Each activity includes its alert status.
  activity_ttl_days: 90
```

### CosmosDB Document Model

All data is stored in Azure CosmosDB across three containers:

**`symbols` container** (partition key: `/symbol`) — four document types:

| Document Type | Purpose | Growth |
|---|---|---|
| `symbol_config` | One per symbol — watchlist flags, positions, metadata | Static (updated, not appended) |
| `activity` | One per symbol per agent run — full analysis output | ~20/day per symbol |
| `alert` | One per actionable activity (SELL, ROLL, CLOSE) | ~1-5/week per symbol |
| `report` | On-demand symbol report — technical analysis, dividends, options, risk | ~1-2/week per symbol |

**`telemetry` container** (partition key: `/metric_type`) — runtime performance stats with 30-day TTL:

| Metric Type | Purpose | Fields |
|---|---|---|
| `tv_fetch` | TradingView page fetch timing | resource, duration_seconds, response_size_chars |
| `agent_run` | End-to-end agent execution timing | agent_type, duration_seconds |

**`settings` container** (partition key: `/id`) — application configuration persistence:

| Document ID | Purpose | Persisted Sections |
|---|---|---|
| `app_config` | Application settings synchronized across all components | `context`, `scheduler`, `web`, `telegram` |

On first run, configuration from `config.yaml` is seeded into the `settings` container (except `azure` and `cosmosdb` sections which remain file-only). On subsequent runs, new keys from `config.yaml` are added to CosmosDB, but existing values are never overwritten, allowing the Settings UI to persist changes. The Settings UI reads and writes directly to CosmosDB, making configuration changes immediately available to all components (scheduler, telegram notifier, web UI) without restart. If CosmosDB is unavailable, `config.yaml` serves as the fallback.

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
  "activity": "SELL",
  "strike": 60.0,
  "expiration": "2026-04-17",
  "iv": 32.5,
  "reason": "IV Rank elevated with strong technical support; selling 30-delta call",
  "confidence": "high",
  "risk_flags": [],
  "risk_rating": 3,
  "risk_rating_breakdown": {
    "volatility": 1,
    "assignment": 0,
    "technical": 1,
    "calendar": 0,
    "sentiment": 1
  }
}
```

For `SELL` activities, `strike`, `expiration`, premium, `risk_rating`, and `risk_rating_breakdown` fields are populated. A corresponding `alert` document is also created with the actionable subset of the activity data.

### Telegram Notifications

When a `SELL`, `ROLL`, or `CLOSE` alert is generated, a Telegram notification is sent if enabled (see [Configuration](#configuration)). The message includes the symbol, action, and key details (strike, expiration, risk flags). Sell alerts additionally include the risk rating (`Risk: X/10`) for at-a-glance risk assessment.

## Dual-Mode Chat Experience

The `/chat` page now offers two distinct modes for analysis:

### Portfolio Chat

Analyze tracked symbols using your CosmosDB watchlist and position data. The chat context includes:
- Recent activities and alerts for the selected symbol
- Open positions (strike, expiration, status)
- Historical decisions and risk flags

Perfect for in-depth analysis of symbols you're actively tracking.

**How to use:**
1. Visit `/chat`
2. Click "Portfolio Chat"
3. Select a tracked symbol or ask general questions about your portfolio
4. Get insights based on your historical data and positions

### Quick Analysis

Analyze any symbol (tracked or not) using live TradingView data, without saving to your database. Quick Analysis fetches:
- Real-time overview (market cap, P/E, dividend yield, etc.)
- Technical indicators (RSI, MACD, moving averages, etc.)
- Analyst forecasts (price targets, ratings)
- Options chain (if available)
- Dividend history

Perfect for researching new symbols before committing to tracking.

**How to use:**
1. Visit `/chat`
2. Click "Quick Analysis"
3. Enter a symbol and select its market (NASDAQ, NYSE, AMEX, OTC)
4. Click "Fetch & Analyze"
5. Chat about the symbol with live data context
6. Use "Change Mode" to switch back to Portfolio Chat or select a different symbol

**Configuration:** Quick Analysis is read-only — data is fetched but never saved to CosmosDB. Rate limiting is handled gracefully with clear error messages.

## Summarization Agent

An optional daily summary agent that sends a Telegram notification with a digest of your portfolio activities. Useful for staying informed without checking the dashboard daily.

### Features

- **Daily Summaries** — Automatically runs on a configurable schedule (default: 8 AM, America/New_York timezone)
- **Per-Symbol Activity Digest** — Summarizes the N most recent activities for each tracked symbol (configurable, default: 3)
- **Configurable Schedule** — Set the cron expression to match your timezone and preferences
- **Enable/Disable Toggle** — Turn on/off without restarting the application
- **Telegram Integration** — Requires Telegram notifications to be enabled; summaries are sent via Telegram

### Configuration

Configure the Summarization Agent in the **Settings** page (`/settings`):

1. **Enable/Disable** — Toggle the agent on/off
2. **Cron Expression** — Set the schedule (e.g., `0 8 * * *` for 8 AM daily)
3. **Activity Count** — Number of recent activities per symbol to include in the summary (1–5)
4. **Timezone** — Uses the global scheduler timezone from `config.yaml` (default: `America/New_York`)

Or configure in `config.yaml`:
```yaml
summary_agent:
  enabled: true
  cron: "0 8 * * *"        # 8 AM daily (America/New_York timezone)
  activity_count: 3         # Latest N activities per symbol
```

### How It Works

1. The summarization agent runs on the configured schedule
2. It queries CosmosDB for all tracked symbols with recent activities
3. For each symbol, it retrieves the N most recent activities and any related alerts
4. The agent uses Azure OpenAI to generate a concise summary of recent decisions and trends
5. A Telegram message is sent with the summary (if Telegram is enabled)
6. The message includes per-symbol activity digests and portfolio-wide insights

### Requirements

- **Telegram Notifications** must be enabled (see `/settings`)
- **Azure OpenAI** credentials configured
- Valid **CosmosDB** connection

If Telegram is disabled, the summary is still generated but not sent.

## Symbol Report

The "Generate Report" button on each symbol's detail page triggers a comprehensive, on-demand analysis report via the **Report Agent** (`src/report_agent.py`).

### What's Included

Each report covers:
- **Technical Analysis** — Current trend direction, price range, and key technical indicators
- **Earnings & Ex-Dividend** — Upcoming dates and their impact on options timing
- **Dividend Summary & Growth** — Yield, payment history, and growth trajectory
- **Options Chain** — Available calls then puts with strikes, premiums, and greeks
- **Open Position Risk Analysis** — Risk assessment for any active positions, including recent activity history
- **Monitoring Agent Recommendations** — Suggested actions based on current market conditions

### How It Works

1. User clicks "Generate Report" on the symbol detail page
2. The Report Agent uses the same `AgentRunner → ChatAgent → AzureOpenAIChatClient` pattern as other agents
3. TradingView data is loaded via the [TradingView Data Cache](#tradingview-data-cache) for fast context assembly
4. The LLM generates a structured report from the system prompt (`src/tv_report_instructions.py`)
5. The report is stored in CosmosDB as a `doc_type="report"` document and displayed on a dedicated page (`/symbols/{symbol}/report`)

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
│   ├── tv_data_fetcher.py                # Hybrid TradingView data fetcher (BeautifulSoup + scanner API for most data, Playwright for options chain)
│   ├── tv_cache.py                       # In-memory TradingView data cache with per-key TTL and async locking
│   ├── covered_call_agent.py             # Covered call wrapper
│   ├── cash_secured_put_agent.py         # Cash secured put wrapper
│   ├── open_call_monitor_agent.py        # Open call position monitor wrapper
│   ├── open_put_monitor_agent.py         # Open put position monitor wrapper
│   ├── report_agent.py                   # Report generation agent wrapper
│   ├── tv_covered_call_instructions.py   # TradingView covered call instructions (no-tools variant)
│   ├── tv_cash_secured_put_instructions.py # TradingView cash secured put instructions (no-tools variant)
│   ├── tv_open_call_instructions.py      # TradingView open call monitor instructions
│   ├── tv_open_put_instructions.py       # TradingView open put monitor instructions
│   ├── tv_open_call_chat_instructions.py # Chat instructions for open call analysis
│   ├── tv_open_put_chat_instructions.py  # Chat instructions for open put analysis
│   ├── tv_report_instructions.py         # Report agent system prompt
│   └── telegram_notifier.py              # Telegram notification service — sends alerts via bot API
├── scripts/
│   └── provision_cosmosdb.sh             # Azure CosmosDB provisioning via az CLI
├── web/
│   ├── __init__.py
│   ├── app.py                            # FastAPI web dashboard — all routes + CosmosDB queries
│   ├── templates/                        # Jinja2 HTML templates (Revolut-inspired dark theme)
│   │   ├── base.html                     # Base layout with nav
│   │   ├── dashboard.html                # Main dashboard — alert overview + activity feed with confidence/agent filters
│   │   ├── alerts.html                  # Alert list for agent+symbol
│   │   ├── alert_detail.html            # Single alert + backing activities
│   │   ├── settings.html                 # Settings (cron expression, error stats)
│   │   ├── symbol_detail.html            # Symbol detail with positions, activities, report/chat buttons, notes, play button
│   │   ├── symbol_report.html            # Per-symbol report display page
│   │   ├── symbol_chat.html              # Per-symbol chat page with context selection
│   │   ├── fetch_preview.html            # Raw data debug/preview page
│   │   └── chat.html                     # Chat interface (dual-mode)
│   └── static/
│       ├── style.css                     # Revolut-inspired dark trading theme CSS
│       └── app.js                        # Client-side JS (row clicks, trigger buttons, filters)
├── run_web.py                            # Web dashboard entry point
├── requirements.txt
├── DESIGN.md                             # UI/UX design reference (Revolut-inspired restyle)
└── README.md
```

## Web Dashboard

- **Dashboard** (`/`) — Alerts overview by agent type with rolling time-range counts (today, last 7 days, last 30 days), scheduler status, recent activity feed with alert indicators and clickable links, position summary. Activities can be filtered by **confidence level** (high/medium/low) and **agent type** for granular views.
- **Alert Details** (`/alerts/{agent}/{symbol}`) — All alerts for a specific symbol, newest first, with activity badges and risk flags.
- **Alert + Activities** (`/alerts/{agent}/{symbol}/{index}`) — Full alert JSON and backing activities from the same time window.
- **Symbol Detail** (`/symbols/{symbol}`) — Full detail page for a symbol: expandable positions with source traceability, editable notes field, Close/Roll/Delete actions, activities, alerts, and "Open Position from Alert" / "Roll Position from Alert" buttons on activity detail. Features a **play button** (▶) for running individual symbol analysis on demand. **Generate Report** and **Chat** buttons are aligned right; watchlist toggles are aligned left. Activities support confidence and agent-type filtering.
- **Symbol Report** (`/symbols/{symbol}/report`) — Dedicated report display page showing the latest generated report for a symbol (technical analysis, dividends, options chain, risk assessment, and recommendations).
- **Symbol Chat** (`/symbols/{symbol}/chat`) — Per-symbol chat page with a context selection screen before starting the conversation. Pre-loads TradingView data via the cache layer for faster responses. Supports open call and open put analysis contexts.
- **Fetch Preview** (`/symbols/{symbol}/fetch-preview`) — Debug page showing raw TradingView data for each resource (overview, technicals, forecast, options chain) with fetch timing and size.
- **Chat** (`/chat`) — Dual-mode chat experience powered by Azure OpenAI:
  - **Portfolio Chat** — Analyze tracked symbols using CosmosDB data (watchlists, positions, recent activities). Click "Portfolio Chat" to ask questions about your tracked symbols.
  - **Quick Analysis** — Analyze any symbol (tracked or not) by fetching live TradingView data without saving to the database. Click "Quick Analysis", select a market (NASDAQ/NYSE/AMEX/OTC), and get instant analysis without committing to tracking.
  - Mode selector on the chat page lets you switch between modes at any time.
- **Settings** (`/settings`) — Scheduler config, Telegram notifications toggle & test button, Summarization Agent config (cron schedule & activity count), runtime stats (today/7d/30d telemetry), TradingView error tracking and anti-403 stats, and a Debug TradingView Fetch tool for testing data fetching per symbol. Settings are persisted to CosmosDB and survive application restarts and deployments. Changes made in the Settings UI are immediately available to all components (scheduler, telegram notifier, summarization agent, etc.) without requiring a restart.

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
playwright install chromium  # Only needed for options chain fetching
```

This installs:
- `agent-framework[foundry]` - Microsoft Agent Framework with Foundry support
- `beautifulsoup4` - HTML parsing for TradingView overview and dividend data
- `requests` - HTTP client for TradingView scanner API (overview, technicals, forecast, dividends)
- `playwright` - Headless Chromium for options chain fetching only (requires browser authentication)
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

# No API key needed for TradingView — overview, technicals, forecast, and dividend data
# is fetched via HTTP requests + TradingView scanner API. Only options chain requires
# Playwright browser automation (for authentication).
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

Build the image (pre-installs Playwright + Chromium for options chain fetching — no Node.js needed):

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

# Create settings container (partition key /id, configuration persistence)
az cosmosdb sql container create \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --database-name "$DATABASE_NAME" \
  --name "settings" \
  --partition-key-path "/id" \
  --partition-key-version 2 \
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

> **Security Tip:** Secure your Container App by configuring authentication with Entra ID or other identity providers. This ensures only authorized users can access your application. For setup instructions, see [Azure Container Apps authentication with Entra ID](https://learn.microsoft.com/en-us/azure/container-apps/authentication-entra).

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
- Playwright is only used for options chain fetching (overview, technicals, forecast, and dividends use HTTP requests + scanner API — Playwright issues won't affect them)
- Ensure Chromium is installed: `playwright install chromium`
- First run may be slow while downloading Chromium
- In Docker, Chromium is pre-installed during image build
- If overview/technicals/forecast/dividends fail, check network connectivity and TradingView scanner API availability

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

TradingView data is fetched via a hybrid approach: overview, technicals, forecast, and dividends use `requests` + `BeautifulSoup` + TradingView scanner API (`scanner.tradingview.com/america/scan2`); options chain uses Playwright with headless Chromium (requires browser authentication). All fetching is driven from Python (`tv_data_fetcher.py`), not by the LLM. The LLM receives pre-fetched data as text and performs analysis only — no tools are given to the agent.

---

## Acknowledgments

This project was built with [GitHub Copilot](https://github.com/features/copilot) and [Squad](https://github.com/bradygaster/squad) by [@bradygaster](https://github.com/bradygaster) — an AI team orchestration framework that runs inside Copilot CLI. Squad coordinated multiple specialized agents to develop, test, and iterate on this codebase.
