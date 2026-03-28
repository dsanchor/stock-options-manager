# Decisions

## Architecture

### [CosmosDB-Centric Refactor](inbox/danny-cosmosdb-refactor-architecture.md) — DETAILED REFERENCE
**Date:** 2026-03-28  
**Author:** Danny (Lead)  
**Status:** Implemented (Phases 1–4a complete)  
**Impact:** Full system — data model, scheduler, web dashboard, config, deployment

**SUMMARY:** Replace file-based data model with symbol-centric CosmosDB backend. Hybrid document model (symbol_config, decision, signal) partitioned by symbol. Includes schema, service layer design, provisioning commands, and 4-phase implementation plan.

⚠️ **Large document (1288 lines).** See `inbox/danny-cosmosdb-refactor-architecture.md` for full specification.

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

## User Directives

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
