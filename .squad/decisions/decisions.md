# Decisions

## Architecture

### [CosmosDB-Centric Refactor](inbox/danny-cosmosdb-refactor-architecture.md)
**Date:** 2026-03-28  
**Author:** Danny (Lead)  
**Status:** Proposed  
**Impact:** Full system — data model, scheduler, web dashboard, config, deployment

Replace file-based data model with symbol-centric CosmosDB backend. Hybrid document model (symbol_config, decision, signal) partitioned by symbol. Includes schema, service layer design, provisioning commands, and 4-phase implementation plan.

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
