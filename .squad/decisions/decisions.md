# Decisions

## Architectural Decisions

### CosmosDB-Centric Refactor
**Date:** 2026-03-28  
**Author:** Danny (Lead)  
**Status:** Implemented (Phases 1–4a complete)  
**Impact:** Full system — data model, scheduler, web dashboard, config, deployment

Replaced file-based data model with symbol-centric CosmosDB backend. Hybrid document model (symbol_config, activity, alert) partitioned by symbol. Includes schema, service layer design, provisioning commands, and 4-phase implementation plan spread across the phases below.

---

## Implementation Phases

### Phase 1: CosmosDB Service Layer
**Date:** 2026-03-28  
**Author:** Rusty (Agent Dev)  
**Status:** ✅ Complete  
**Impact:** Foundation for all downstream work

Implemented the CosmosDB foundation per Danny's architecture doc (Sections 2, 3, 6).

**Deliverables:**
- **`src/cosmos_db.py`** — `CosmosDBService` class with 18 methods covering: symbol config CRUD, watchlist queries, position management, decision/signal write, context-injection reads, and dashboard queries.
- **`src/context.py`** — `ContextProvider` adapter replacing `logger.py` read functions with CosmosDB-backed equivalents. Output format identical (reason-per-line, oldest-first) so agent instructions require no changes.
- **Modified `src/config.py`** — Added `cosmosdb_endpoint`, `cosmosdb_key`, `cosmosdb_database`, `decision_ttl_days` properties. Removed per-agent config sections.
- **Modified `config.yaml`** — Added `cosmosdb` section with env var substitution. Added `decision_ttl_days: 90`. Removed legacy agent config sections.
- **Modified `requirements.txt`** — Added `azure-cosmos>=4.7.0`.

**Key Design Decisions:**
- TTL on decisions (configurable 0–90 days); signals have no TTL (audit trail)
- Backward-compatible context format
- Client-side position filtering to avoid complex CosmosDB queries

---

### Phase 2: Scheduler + Agent Runner Refactor
**Date:** 2026-03-28  
**Author:** Rusty (Agent Dev)  
**Status:** ✅ Complete  
**Impact:** Scheduler fully cloud-backed; file-based symbol/position discovery replaced

Completed CosmosDB migration of scheduler, agent runner, and all four agent wrappers.

**Deliverables:**
- **`src/agent_runner.py`** — Removed file-based symbol/position discovery. Added `run_symbol_agent()` and `run_position_monitor()` functions. Context injection via `ContextProvider.get_context()` (last N decisions with embedded signal status). Decision/signal persistence via `cosmos.write_decision()` / `write_signal()`.
- **`src/main.py`** — Scheduler initializes `CosmosDBService` and `ContextProvider` during setup. All agent wrappers receive cosmos + context_provider.
- **Agent Wrappers (4 files)** — `covered_call_agent.py`, `cash_secured_put_agent.py`, `open_call_monitor_agent.py`, `open_put_monitor_agent.py` — All query CosmosDB for symbols/positions; each wrapper owns a shared `TradingViewFetcher` for browser session reuse.
- **`web/app.py`** — Updated `_run_agent_in_background()` to pass scheduler.cosmos and scheduler.context_provider.

**Key Design Decisions:**
- Fetcher lifecycle: One per agent type per run (not per symbol) for browser session reuse
- Signals embedded in decisions via `is_signal` field per user directive
- `logger.py` deprecated but not removed (backward compatibility)

---

### Phase 3: Web Dashboard CosmosDB Refactor
**Date:** 2026-03-28  
**Author:** Rusty (Agent Dev)  
**Status:** ✅ Complete  
**Impact:** Dashboard fully CRUD-based; file I/O removed

Completed web dashboard refactor from file-based data to CosmosDB-backed REST API.

**Deliverables:**
- **New `web/templates/symbols.html`** — Symbol management UI with toggle switches and add/delete functionality
- **New `web/templates/symbol_detail.html`** — Symbol detail page with position management and recent decisions/signals
- **`web/app.py`** — Complete rewrite: removed JSONL/txt reads, added REST API endpoints, CosmosDB startup init
- **`web/templates/base.html`** — Added "Symbols" nav link
- **`web/templates/dashboard.html`** — Updated row links to `/symbols/{symbol}`, error banner support
- **`web/templates/settings.html`** — Simplified to cron-only + CosmosDB diagnostics
- **`web/static/style.css`** — Added toggle switch, form, button styles

**API Endpoints Added:**
- `GET/POST /api/symbols` — List/create symbols
- `GET/PUT/DELETE /api/symbols/{symbol}` — Symbol CRUD
- `POST /api/symbols/{symbol}/positions` — Add position
- `PUT /api/symbols/{symbol}/positions/{id}/close` — Close position
- `DELETE /api/symbols/{symbol}/positions/{id}` — Delete position
- `GET /api/signals` — List signals (filterable)
- `GET /api/decisions` — List decisions (filterable)

**Removed:** `DATA_FILES` dict, file-based helpers, legacy routes

---

### Phase 4a: Provisioning, Dockerfile, README
**Date:** 2026-03-28  
**Author:** Basher (Tester)  
**Status:** ✅ Complete  
**Impact:** System ready for Azure production deployment

Created provisioning scripts and updated deployment documentation.

**Deliverables:**
- **`scripts/provision_cosmosdb.sh`** — Idempotent az CLI script per architecture Section 8. Serverless default, custom indexing policy, outputs endpoint + key.
- **`scripts/migrate_to_cosmosdb.py`** — Full migration per architecture Section 7.1. Reads 4 data/*.txt + 8 logs/*.jsonl; idempotent; progress output.
- **`Dockerfile`** — Removed `mkdir -p data logs`, added `COPY scripts/ scripts/`, kept playwright install.
- **`README.md`** — Comprehensive rewrite: updated architecture, flow diagrams, config examples, Docker examples, added CosmosDB setup section, migration guide, troubleshooting.

**Key Design Decision:** Migration script is coded against `CosmosDBService` interface. If method signatures change, migration script must be updated to match.

---

---

## Domain Model

### Entity Rename: decision → activity, signal → alert
**Date:** 2025-03-29  
**Authors:** Danny (Lead), Rusty (Agent Dev), Linus (Quant Dev)  
**Status:** Completed  
**Impact:** Full system — backend, frontend, instructions, documentation  

The codebase used two domain concepts that were causing confusion:
- "decision" — Agent output for every symbol/position analysis
- "signal" — Actionable subset of decisions (SELL, ROLL, CLOSE)

These terms were ambiguous and overloaded. Renamed comprehensively across the entire system:

- **"decision" → "activity"** — Better reflects that these are agent actions/outputs, not decisions
- **"signal" → "alert"** — Clarifies these are actionable notifications, distinct from trading signals
- **"is_signal" → "is_alert"** — Boolean flag in documents
- **"max_decision_entries" → "max_activity_entries"** — Config key
- **"decision_ttl_days" → "activity_ttl_days"** — Config key

**Implementation:**
- **Backend (Rusty):** Renamed across 11 Python files (cosmos_db.py, agent_runner.py, context.py, config.py, 4 agent wrappers, scripts/provision_cosmosdb.sh), config.yaml. Preserved OS signal handling in main.py (SIGINT, SIGTERM).
- **Frontend (Linus):** Renamed across web/app.py (1412 lines), 6+ templates (decision_detail.html → activity_detail.html, signal_detail.html → alert_detail.html, signals.html → alerts.html), CSS classes, display text, API routes.
- **Instructions (Danny):** Updated agent instruction files (tv_*_instructions.py), README.md, documentation examples.
- **Database:** Recreated from scratch; no migration needed.

**Verification:** Zero "decision" or "signal" references remain in backend (except OS signals); zero remaining in frontend display text or CSS classes.

---


### Context Injection for Agent Execution (2026-03-28T13:48)
**By:** dsanchor (via Copilot)  
**What:** Include last 2 decisions (configurable 0–5) for the symbol/position being analyzed. Signals embedded in decisions, not separate context.  
**Why:** Simplifies context injection model. Decisions are primary unit.  
**Impact:** Changes `src/context.py`, `config.yaml` defaults, agent runner context injection logic.  
**Status:** ✅ Implemented in Phase 2

---

## Backend / Infrastructure

### Switch from Azure CLI Credential to API Key Authentication
**Date:** 2025-07-XX  
**Decider:** Rusty (Backend Dev)  
**Status:** Implemented

Switched from `AzureCliCredential` to API key authentication for Azure OpenAI. Simpler user setup (env var only), better Docker compatibility, reduced dependencies. Updated `AzureOpenAIChatClient`, `config.yaml`, `requirements.txt`, and documentation.

**Files Changed:** `src/agent_runner.py`, `src/config.py`, `src/main.py`, `config.yaml`, `requirements.txt`, `README.md`  
**Commit:** c502632

---

### Scheduler ↔ Web Communication via app.state
**Date:** 2025-07-22  
**Author:** Rusty (Agent Dev)  
**Impact:** Architecture (scheduler + web coupling)

Store `_scheduler_instance` on `app.state.scheduler` during FastAPI lifespan startup. Web routes access via `request.app.state.scheduler`. Degrades gracefully in `--web-only` mode (trigger returns 503, cron saves to YAML).

---

### Dockerfile Architecture for Playwright MCP
**Date:** 2025-07  
**Agent:** Rusty  
**Status:** Implemented

Single-stage `python:3.12-slim` base with Node.js installed via NodeSource. Pre-caches Playwright browsers during build. ENTRYPOINT pattern allows natural flag appending (`--web-only`, `--port`).

**Volume mount contract:**
- `data/` — Watchlists, position files (read-write)
- `logs/` — JSONL decision/signal logs (read-write)
- `config.yaml` — Configuration (read-only)
- `~/.azure` — Azure CLI credentials (read-only)

---

## Web Dashboard

### Dashboard Data Enrichment from Decision Logs
**Date:** 2025-07-28  
**Author:** Rusty  
**Status:** Implemented  
**Commit:** 0831a03

`_build_agent_table()` reads `decision_log` via `_latest_decisions_by_key()` to enrich dashboard rows with health metrics (DTE, moneyness, delta, IV, premium, risk flags). Signal list page gains IV/Premium/Delta columns.

---

### Render-time Signal Enrichment from Decisions
**Author:** Rusty  
**Date:** 2025-07  
**Status:** Implemented

Enrich signals at render time in `web/app.py` by matching each signal to the closest decision (same symbol key, ±2 hour window). Helper `_enrich_signal_from_decisions()` copies only missing fields. Keeps signal JSONL compact.

---

## Logging / Data

### Timestamp Generation Moved from LLM to Python
**Author:** Rusty (Agent Dev)  
**Date:** 2025-07-28  
**Status:** Implemented  
**Commit:** 54a219e

All log timestamps now set in Python BEFORE agent execution using `TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"`. LLM's `timestamp` field is always overridden. Ensures consistency across decision and signal JSONL logs.

**Impact for team:**
- **Linus (Quant Dev):** Instruction schemas still include `timestamp` but as "auto-set by system"
- **Basher (Test/Ops):** All log entries now have consistent `YYYY-MM-DD HH:MM:SS` format

---

## User-Facing Features

### Position-from-Decision Endpoint: Inline Watchlist Disable + Cascade Delete
**Date:** 2026-03-29  
**Author:** Rusty (Agent Dev)  
**Status:** ✅ Implemented  
**Impact:** API endpoint, data model, decision lifecycle

Implemented `POST /api/symbols/{symbol}/positions/from-decision/{decision_id}` endpoint to open positions directly from decision intelligence. Extended `cosmos_db.py` `add_position()` with `source` parameter to track position origin (decision vs. watchlist).

**Design Decision:** Endpoint performs watchlist disable and cascade-delete inline rather than extracting shared logic with `api_update_symbol`. This keeps flows independent and avoids coupling user-initiated "open position" action with general symbol updates. Trade-off: watchlist-disable logic must be maintained in two places if it changes.

**Files Modified:**
- `src/cosmos_db.py` — `add_position()` source parameter
- `web/app.py` — New endpoint with inline watchlist/cascade logic

---

### Expandable Position Rows + Open Position Button
**Date:** 2026-03-29  
**Author:** Linus (Quant Dev)  
**Status:** ✅ Implemented  
**Impact:** Web dashboard UX

Added "Open Position" button to decision detail view (signal banner, Jinja conditional). Implemented expandable position rows in symbol detail via hidden `<tr class="pos-detail-row">` elements toggled by row click. Event propagation guard prevents expand/collapse when clicking action buttons. Reused existing CSS (`detail-grid`, `detail-field`) for visual consistency. Table now 8 columns (added chevron affordance column).

**Design Decisions:**
1. Button placed in signal banner flexbox (keeps signal indicator and CTA visually paired)
2. `<tr>` expansion with `display:none` toggle (maintains table semantics)
3. `e.target.closest()` guard for action buttons (more robust than `stopPropagation()`)
4. Reused existing CSS classes (ensures visual consistency)
5. Colspan = 8 (added chevron column)

**Trade-offs:**
- Inline styles for detail panel (border, padding) instead of new CSS classes
- Agent type formatting via inline Jinja ternary (would benefit from custom filter if more agent types added)

**Files Modified:**
- `web/templates/decision_detail.html` — Open Position button + scripts
- `web/templates/symbol_detail.html` — Expandable position rows + expand/collapse logic

---

## Frontend Features

### Price Chart Implementation on Symbol Detail Page
**Date:** 2025-07-25  
**Author:** Linus (Quant Dev)  
**Status:** Implemented  

Added a candlestick price chart with activity/alert markers on the symbol detail page to provide a visual timeline of agent activity relative to price movements.

**Charting Stack:**
- **Library:** TradingView Lightweight Charts (CDN, ~40KB, Apache 2.0)
- **Price Data:** yfinance (3-month daily OHLC, runs in asyncio.to_thread() for non-blocking)
- **Markers:** CosmosDB activities + alerts with visual distinction (⚡ amber for alerts, 📊 gray for activities)
- **New Endpoint:** `GET /api/symbols/{symbol}/chart-data` returns `{"candles": [...], "markers": [...]}`

**Files Changed:**
- `web/app.py` — new `/api/symbols/{symbol}/chart-data` endpoint
- `web/templates/symbol_detail.html` — chart card + Lightweight Charts script
- `requirements.txt` — added `yfinance>=0.2.0`

---

### Manual Roll UI in Positions Table
**Date:** 2025-07-24  
**Author:** Linus (Quant Dev)  
**Status:** Implemented  

Added an inline roll form inside the expandable position detail row rather than a modal dialog. The Roll button in the actions column expands the row and reveals the form at the top of the detail panel.

**Design:** Pre-populates form with current strike/expiration so users only need to adjust.

**API Contract:** `POST /api/symbols/{symbol}/positions/{position_id}/roll` with body `{"new_strike": 150.0, "new_expiration": "2025-08-15", "notes": "optional"}`

**Signal Table Enhancement:** Conditionally shows `(from $X)` context for roll signals with `new_strike`/`current_strike` and `new_expiration`/`current_expiration` fields.

---

### Roll Position Frontend — Conditional Buttons + Closing Source Display
**Date:** 2025-07-15  
**Author:** Linus (Quant Dev)  
**Status:** Implemented  

Button type in `decision_detail.html` determined by `decision.agent_type` at render time via Jinja conditional. Roll button calls `POST /roll-from-decision/`; Open button calls `POST /from-decision/`. Symbol detail page expands rows to show `closing_source`, `rolled_from`, `rolled_to` metadata.

---

## Performance & Reliability

### Chat Context Preload Pattern
**Date:** 2025-07  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  

Per-symbol chat previously fetched CosmosDB config + last 5 activities + TradingView data on every message (~5-10s latency). Split into two endpoints:

1. `POST /api/symbols/{symbol}/chat/context` — Heavy data fetch, runs once on page load
2. Chat message endpoint — Accepts optional `context` field, uses cached context if provided

**Result:** Chat response time drops from ~8-12s to ~2-3s per message (after initial load).

**Key Choices:**
- POST (TradingView fetch is a side effect)
- Optional context field (backward compatible)
- Extracted helpers `_build_symbol_context()` and `_build_symbol_system_prompt()` to avoid duplication

---

### Eager CosmosDB Connection Validation at Startup
**Date:** 2025-07-14  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  

Azure Cosmos DB Python SDK's `CosmosClient()` is lazy — doesn't connect until first query. Added `cosmos.database.read()` immediately after construction to force eager HTTP call, surfacing connection/auth errors at startup instead of on first user request.

**Trade-offs:**
- Pro: Failures caught at startup with full traceback; error stored in `app.state.cosmos_error` for settings page
- Con: Adds ~200ms to startup time; if CosmosDB is temporarily unreachable at startup, app won't self-heal without restart

**Files Changed:**
- `web/app.py` — startup handler, `_resolve_env`, `_get_cosmos`, settings/dashboard routes
- `web/templates/settings.html` — error diagnostic section

---

## Data Management

### Runtime Telemetry Infrastructure
**Date:** 2025-07  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  

Added a second CosmosDB container (`telemetry`) to track runtime performance stats for agent executions and TradingView data fetching.

**Design:**
- Separate container with partition key `/metric_type` (operational data separate from business data)
- Best-effort initialization (system works without telemetry container)
- 30-day TTL on all telemetry docs (per-document, no manual cleanup)
- Fetcher stores stats in `self.last_fetch_stats`; caller (AgentRunner) handles write (decoupling)
- Telemetry writes post-execution in separate try/except (never masks real errors)
- Python-side aggregation for `get_telemetry_stats()`

**Impact:** Settings page shows runtime stats card; no changes to agent logic or activity/signal flow.

---

### Position Rollover Design

#### Roll Position — Atomic Single-Write
**Date:** 2025-07  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  

Rolling a position (close old, open new with same monitor-agent signal) happens with a single in-memory operation and single `replace_item` CosmosDB call. Avoids partial-write states.

**Traceability:**
- Old position → `rolled_to: <new_position_id>` + `closing_source: {snapshot}`
- New position → `rolled_from: <old_position_id>` + `source: {snapshot}`
- Both snapshots reference same `decision_id` from monitor signal

**No Watchlist/Cascade Side Effects:** Unlike "Open Position from Activity" (watch agents), roll endpoint does NOT disable watchlist flags or cascade-delete activities. Monitor agents track open positions — disabling would break monitoring.

---

#### Manual Roll Endpoint Design
**Date:** 2025-07-16  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  

Made `source`/`closing_source` optional in `roll_position()` rather than creating separate method. One code path for both manual and signal-based rolls.

**Design:** Endpoint infers position type (call/put) from existing position instead of requiring caller to specify it — fewer fields to pass, fewer validation errors.

**Endpoint:** `POST /api/symbols/{symbol}/positions/{position_id}/roll`

---

#### Cascade runs after watchlist flag is persisted
**Date:** 2025-07  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  

When a user toggles off a watchlist agent (covered_call or cash_secured_put), cascade delete runs AFTER the watchlist document update is persisted (`replace_item`), not before.

**Reasoning:** If cascade fails mid-way, flag is already `False` — UI correctly shows agent as disabled. Orphaned activities/signals are harmless and would be cleaned up on subsequent toggle-off or manual cleanup.

---

## Documentation & Deployment

### Unified Azure Setup Documentation
**Date:** 2025-07-15  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  

Merged separate Azure provisioning sections into single "## Azure Setup" with five numbered steps in logical dependency order:

1. Set Variables (consistent `${VAR:-default}` pattern)
2. Create Resource Group (once, shared)
3. Provision CosmosDB (inline az CLI commands)
4. Deploy to Container Apps (uses CosmosDB outputs from step 3)
5. Update Deployment (for subsequent pushes)

**Rationale:** Prevents users from deploying Container Apps before CosmosDB; eliminates drift; consistent variable patterns; `eastus` unified default.

**Impact:** README.md refactored (~48 net line reduction); `provision_cosmosdb.sh` unchanged.

---

### Remove Old File-Based Storage Artifacts
**Date:** 2025-07-09  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  

Deleted all file-based storage artifacts after CosmosDB migration completed:
- `data/` directory
- `logs/` directory
- `src/logger.py`
- `scripts/migrate_to_cosmosdb.py`

**Rationale:** Dead code/files create confusion; migration script references deleted data formats; README referenced file-based workflows no longer in use.

---

## Logging

### Timestamp Generation Moved from LLM to Python
**Date:** 2025-07-28  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  
**Commit:** 54a219e

All log timestamps set in Python BEFORE agent execution using `TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"`. LLM's `timestamp` field always overridden. Ensures consistency across activity and alert JSONL logs.

**Impact for team:**
- Linus (Quant Dev): Instruction schemas still include `timestamp` but as "auto-set by system"
- Basher (Test/Ops): All log entries now have consistent `YYYY-MM-DD HH:MM:SS` format

---

### Dashboard Data Enrichment from Activity Logs
**Date:** 2025-07-28  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  
**Commit:** 0831a03

`_build_agent_table()` reads `activity_log` via `_latest_activities_by_key()` to enrich dashboard rows with health metrics (DTE, moneyness, delta, IV, premium, risk flags). Alert list page gains IV/Premium/Delta columns.

---

### Render-time Alert Enrichment from Activities
**Date:** 2025-07  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  

Enrich alerts at render time in `web/app.py` by matching each alert to the closest activity (same symbol key, ±2 hour window). Helper `_enrich_alert_from_activities()` copies only missing fields. Keeps alert JSONL compact.


---

## User Directives

### Only Commit Changes, Never Push
**Date:** 2026-03-30T11:27:20Z  
**By:** dsanchor (via Copilot CLI)  
**Status:** Active  

User directive: Only commit changes automatically, never push to remote. User will handle `git push` manually.

**Rationale:** User workflow preference to maintain control over when changes go to remote.

---

## Web UI & Frontend Decisions

### Dashboard Timezone Display Pattern
**Date:** 2024-03-30  
**Author:** Linus (Quant Dev / Frontend)  
**Status:** Implemented  

Implement dual-timezone display on dashboard for scheduler "Last run" and "Next run" times to reduce user confusion across timezones.

**Design:**
1. **Primary:** Show times in scheduler's configured timezone (backend provides ISO timestamp + timezone name)
2. **Secondary:** If user's browser timezone differs, show their local time below in smaller, muted text
3. **Tooltip:** Hover shows both times clearly labeled

**Implementation Pattern:**
- Backend passes: `{field}_iso` (ISO 8601 string) and `scheduler_timezone` (IANA timezone name)
- Frontend: Client-side JavaScript uses `toLocaleString()` with timezone parameter
- Format: "MMM DD, YYYY, HH:MM:SS AM/PM TZN" (e.g., "Mar 30, 2024, 02:00:00 PM EDT")
- Dual display markup: `formatted + '<br><small style="color: #888;">(localFormatted)</small>'`

**Rationale:**
- **Clarity:** No ambiguity about which timezone is displayed
- **Convenience:** Users see times in their local context when relevant
- **Clean UI:** Single timezone display when user TZ = scheduler TZ (no clutter)
- **Standards-based:** Uses native Intl API, no external timezone libraries needed client-side
- **Maintainable:** Backend owns timezone logic, frontend just formats for display

**Team Impact:**
- **Pattern:** Can be reused for any timestamp display in web UI
- **Backend contract:** Always send `{field}_iso` (ISO string) + timezone name
- **Frontend contract:** Always format client-side using Intl API

**Files Modified:** `web/templates/dashboard.html`

**Related:** Backend timezone support added by Rusty (pytz integration in web/app.py); scheduler timezone configuration in config.yaml and Settings page

---

## Data Fetching & Backend Architecture

### Refactor TradingView Fetchers from Playwright to BeautifulSoup + Scanner API
**Date:** 2026-07-14  
**Author:** Rusty (Backend Dev)  
**Status:** Implemented  
**Impact:** Performance, reliability, resource usage  

All 5 TradingView data fetchers in `src/tv_data_fetcher.py` used Playwright (headless Chromium) to load full pages and extract innerText. This was heavyweight — every fetch launched browser tabs, waited for networkidle, and pulled raw unstructured text.

Test scripts (`test/test_fetcher.py`, `test/test_dividends_fetcher.py`, `test/test_technicals_fetcher.py`, `test/test_forecast_fetcher.py`) proved that 4 of 5 resources could be fetched via plain HTTP requests + BeautifulSoup, with a scanner API fallback.

**Decision:** Switch overview, technicals, forecast, and dividends to **requests + BeautifulSoup + TradingView scanner API**. Keep Playwright **only** for options chain (requires browser-level API interception).

**Key Changes:**
1. **4 fetchers refactored** — multi-strategy: HTML extraction → embedded JSON → scanner API
2. **Options chain unchanged** — still Playwright with response interception
3. **Lazy browser init** — Playwright only starts when options chain is needed
4. **Structured JSON output** — fetchers return `json.dumps()` with typed fields instead of raw page text
5. **Added `beautifulsoup4>=4.12.0`** to requirements.txt

**Trade-offs:**
- **Pro:** ~10x faster fetches (no browser startup), lower memory, structured data for LLM analysis
- **Pro:** Playwright failure modes (timeouts, consent banners, JS rendering) eliminated for 4/5 resources
- **Pro:** Lazy browser means Playwright isn't loaded at all when options chain isn't requested
- **Con:** Depends on TradingView's HTML structure / scanner API stability (same as test scripts)
- **Con:** Return format changed from plain text to JSON string — callers that parsed raw text may need adjustment (current callers just pass strings through, so no impact)

**Implications:**
- All callers (`agent_runner.py`, `web/app.py`, agent wrappers) are unchanged — they call `fetch_all()` which returns `dict[str, str]`
- LLM agents now receive structured JSON instead of raw page dumps — potentially better analysis quality
- If TradingView changes their scanner API or page structure, the 3-strategy fallback provides resilience

---

### CosmosDB Settings Must Override Config File at Runtime
**Date:** 2025-01-15  
**Author:** Rusty (Backend Dev)  
**Status:** Implemented  

The application uses a two-tier configuration system:
1. **config.yaml** — File-based defaults
2. **CosmosDB settings** — Runtime-editable settings via web UI

The `merge_defaults()` function merges config.yaml values into CosmosDB, but only adds missing keys (never overwrites existing CosmosDB values).

**Problem:** After merge_defaults() was called in `src/main.py`, the Config object was NOT updated with the merged result. This caused the scheduler to use stale values from config.yaml instead of the authoritative CosmosDB values.

**Symptom:** User sets cron to "30 9-16/4 * * 1-5" via web UI → CosmosDB correctly stores it → but scheduler runs with "00 9-16/4 * * 1-5" from config.yaml.

**Decision:** After calling `merge_defaults()`, immediately update the Config object with the merged settings:

```python
merged_settings = self.cosmos.merge_defaults(settings_defaults)

# Update Config object with merged settings from CosmosDB (CosmosDB takes precedence)
if merged_settings:
    for key, value in merged_settings.items():
        if key not in ('azure', 'cosmosdb'):
            self.config.config[key] = value
```

**Rationale:**
1. **CosmosDB is the source of truth** for runtime-editable settings
2. **config.yaml is for defaults only** (first-run seed + new keys added in code updates)
3. **Web UI changes must persist** across scheduler restarts
4. **merge_defaults() returns the merged result** — we must use it

**Impact:**
- Scheduler now correctly uses settings modified via web UI
- Settings precedence is clear: CosmosDB > config.yaml
- No breaking changes — only fixes broken behavior

**Files Modified:** `src/main.py` — OptionsAgentScheduler.setup()

**Testing:** Set cron to "30 9-16/4 * * 1-5" via web UI, restart scheduler, verify it prints and uses the :30 minutes.

---

### Use Playwright Locators for Targeted Data Extraction
**Date:** 2025-01-XX  
**Author:** Rusty (Backend Dev)  
**Status:** Implemented  

The original `fetch_overview` method grabbed the entire `#tv-content` innerText, which returned excessive noise. We needed a more surgical approach to extract only the "Fundamentals and stats" section.

**Decision:** Rewrote `fetch_overview` to use Playwright's locator API:
1. Locate the H1 element containing "Fundamentals and stats"
2. Traverse to its parent container using `.locator('..')`
3. Extract only that container's inner text

**Implementation Details:**
- Uses `page.locator('h1:has-text("Fundamentals and stats")').locator('..')`
- Includes fallback to old `_fetch_page_text()` approach if locator fails
- Maintains retry wrapper compatibility
- Proper page lifecycle management with finally block

**Rationale:**
- Reduces noise in overview data by targeting specific DOM section
- More resilient than hardcoded CSS selectors (semantic text-based targeting)
- Graceful degradation ensures system doesn't break if page structure changes
- Pattern can be applied to other fetch methods if similar issues arise

**Impact:**
- Overview data should be cleaner and more focused on fundamental metrics
- Slightly more complex code, but better failure handling with fallback
- No breaking changes to return format or API contract

**Future Considerations:**
- Monitor success rate of locator approach vs. fallback usage
- Consider applying same pattern to `fetch_technicals`, `fetch_forecast`, etc. if they have similar noise issues
- If TradingView changes page structure, fallback ensures continuity

---

## Feature Implementations

### Per-Symbol Telegram Notification Toggles
**Date:** 2025-01-15  
**Author:** Rusty (Backend Dev)  
**Type:** Feature Implementation  
**Status:** Implemented  

Implemented per-symbol toggle for Telegram notifications to give users fine-grained control over which symbols trigger alerts.

**Context:** User requested ability to disable Telegram notifications for specific symbols while keeping notifications enabled for others. This is particularly useful when:
- User has many symbols but only wants alerts for a subset
- Testing new symbols without spam
- Temporarily muting notifications for volatile symbols

**Implementation Approach:**

**1. Storage Pattern:**
- Added `telegram_notifications_enabled: bool` field to symbol config documents in CosmosDB
- Default value: `True` (preserves existing behavior)
- Follows same pattern as `covered_call`/`cash_secured_put` watchlist toggles

**2. Notification Check Location:**
- Check implemented in `TelegramNotifier.send_alert()` method (not agent runners)
- **Rationale:** Centralizing the check ensures ALL notification types (sell alerts, roll alerts, future types) respect the setting without modifying multiple agent codepaths

**3. Safe Defaults:**
- Missing field = enabled (backward compatible)
- Symbol not found = enabled (fail open, not closed)
- CosmosDB unavailable = enabled (graceful degradation)

**4. UI Placement:**
- Toggle appears next to Call/Put watchlist toggles
- Labeled "Telegram Notifications" for clarity
- Present on both symbols list page and symbol detail page

**Migration:**
Existing symbols need the field added. Run:
```bash
python scripts/migrate_add_telegram_notifications.py
```
This adds `telegram_notifications_enabled: True` to all existing symbols.

**Files Modified:**
- `src/cosmos_db.py` — Symbol schema
- `src/telegram_notifier.py` — Notification check logic
- `web/app.py` — API endpoint handler
- `web/templates/symbol_detail.html` — Detail page toggle
- `web/templates/symbols.html` — List page toggle
- `scripts/migrate_add_telegram_notifications.py` — Migration script

**Alternative Approaches Considered:**
1. **Global blacklist in settings:** Rejected — less discoverable, harder to manage per-symbol
2. **Agent-level check:** Rejected — would require modifying all agent runners, not future-proof
3. **Separate notification config document:** Rejected — adds complexity, symbol config is the natural place

**Future Considerations:**
- Could extend to notification types (e.g., "disable sell alerts but keep roll alerts")
- Could add notification frequency limits per symbol
- Could integrate with "quiet hours" feature if added

**Team Impact:**
- **Danny:** Frontend toggle follows existing patterns
- **Linus:** No impact on agent strategy logic
- **All:** Existing symbols remain opt-in (notifications enabled) after migration

