# Danny — History

## Project Context
- **Project:** options-agent
- **User:** dsanchor
- **Stack:** Python, Microsoft Agent Framework, Azure Foundry (gpt-5.1)
- **MCP:** iflow-mcp_ferdousbhai_investor-agent 1.6.3
- **Description:** Two periodic trading agents for covered call and cash-secured put sell signals. Local runtime, configurable polling, stock symbols from files, decision logs, sell signal alerts.

## Learnings

### CosmosDB Settings Container Documentation (2026-03-30)
- Updated `README.md` with comprehensive "Settings Container" section covering:
  - Feature overview with deep-merge behavior and use cases
  - Setup and initialization instructions (automatic on first run)
  - Configuration API reference with endpoint signatures
  - Example JSON payloads for nested config updates (e.g., telegram settings)
  - Troubleshooting guide for common configuration issues
  - Cross-reference to Rusty's implementation details
- Documentation included in commit fa64388 alongside implementation.
- Ensures users can independently manage runtime configuration via API or file.

### Telegram Documentation (2026-03-29)
- Updated `README.md` with:
  - **Telegram Alerts section** — Feature overview (real-time decision/alert notifications)
  - **Setup Instructions** — Step-by-step: BotFather, channel ID, env vars/config.yaml, test via /settings
  - **Configuration Reference** — Schema: `telegram.bot_token`, `telegram.channel_id`, `enabled`
  - **Project Structure** — Added `src/telegram_notifier.py`, `.squad/orchestration-log/`, `.squad/log/` references
- Commit: 4e1c16c.

### 2026-03-27: Model Configuration Updated to gpt-5.1

**User Directive (dsanchor):** Updated model from gpt-5.4-mini to gpt-5.1 in config/team.md

**Reason:** gpt-5.1 shows superior performance on multi-step TradingView Playwright workflows (navigate → click → snapshot sequences for options chain extraction). gpt-5.4-mini struggled with complex sequential browser instructions.

**Impact for Danny's Work:**
- Any downstream systems consuming agent outputs should verify compatibility with gpt-5.1 decision quality
- Model change applies to all providers (Massive.com, Alpha Vantage, TradingView) via team config inheritance
- Output format remains consistent (JSON+SUMMARY as per Rusty's 2026-03-27 update)
- No API contract changes, only model selection in config

**Status:** ✅ Updated in config/team.md
**Team:** User directive (dsanchor), Rusty (config implementation)

### 2026-03-28: CosmosDB-Centric Architecture Design (IMPLEMENTED)

**User Directive (dsanchor):** Full architectural refactor from file-based to CosmosDB-backed symbol-centric data model.

**Key Architecture Decisions:**

1. **Hybrid document model (NOT single-document-per-symbol):** A single document would exceed CosmosDB's 2MB limit within months for active symbols (~7MB/year in decisions alone). Instead: partition key = symbol ticker, with 3 document types in one container: `symbol_config`, `decision`, `signal`.

2. **Partition key = symbol ticker:** All queries for a symbol (config + decisions + signals) are single-partition and fast. Cross-partition queries only for dashboard aggregation (low QPS, acceptable).

3. **Positions embedded in symbol_config:** Positions per symbol are few (<20), so embedding avoids extra document lookups. Position lifecycle: `active` → `closed`.

4. **Serverless CosmosDB default:** Low traffic (50 operations/day). Pennies per month. Autoscale provisioned is the upgrade path.

5. **TTL on decision documents (90 days):** Prevents unbounded growth. Signals kept indefinitely for audit trail.

6. **Context injection adapter pattern:** `src/context.py` wraps CosmosDB reads and returns formatted strings identical to the old `logger.py` output. Agent instructions remain unchanged.

7. **Agent runner no longer owns discovery:** Scheduler queries CosmosDB for enabled symbols/positions and passes them individually to the runner. Runner only handles single-symbol execution.

**Implementation Status: ✅ COMPLETE (all 4 phases delivered as of 2026-03-28)**

**Implementation Timeline:**
- **Phase 1 (2026-03-28T13:50):** Rusty — CosmosDB service layer + config. Created `src/cosmos_db.py` (18 methods), `src/context.py`, updated config layer. Orchestration log: `2026-03-28T1350-rusty-phase1.md`
- **Phase 2 (2026-03-28T13:55):** Rusty — Scheduler + agent runner refactor. Refactored `main.py`, `agent_runner.py`, all 4 agent wrappers to use CosmosDB. Orchestration log: `2026-03-28T1355-rusty-phase2.md`
- **Phase 3 (2026-03-28T14:00):** Rusty — Web dashboard refactor. Rewrote `web/app.py` with REST API, created `symbols.html`, `symbol_detail.html`. Orchestration log: `2026-03-28T1400-rusty-phase3.md`
- **Phase 4a (2026-03-28T13:50):** Basher — Provisioning + deployment. Created `scripts/provision_cosmosdb.sh`, `scripts/migrate_to_cosmosdb.py`, updated Dockerfile, comprehensive README. Orchestration log: `2026-03-28T1350-basher-phase4a.md`

**Key Files Modified/Created:**
- Architecture doc: `.squad/decisions/inbox/danny-cosmosdb-refactor-architecture.md` (kept as reference, 1288 lines)
- Modules: `src/cosmos_db.py`, `src/context.py`, `scripts/migrate_to_cosmosdb.py`, `scripts/provision_cosmosdb.sh`
- Modified: `config.yaml`, `src/config.py`, `src/agent_runner.py`, `src/main.py`, `web/app.py`, all 4 agent modules, Dockerfile, README.md
- Deprecated: `src/logger.py`, `data/*.txt`, `logs/*.jsonl`
- New web templates: `web/templates/symbols.html`, `web/templates/symbol_detail.html`
- New dependency: `azure-cosmos>=4.7.0`

**User Preferences (dsanchor):**
- Prefers comprehensive design docs with actual schemas, code, and CLI commands ✅
- Wants everything symbol-centric (per-symbol settings, not global config files) ✅
- Only global setting = cron expression ✅
- Full CRUD through web dashboard ✅

**Team Cross-References:**
- **Rusty (Agent Dev):** Implemented all 3 phases (service layer, scheduler, web); architecture fully realized
- **Basher (Tester):** Implemented provisioning phase; tested phases 1–3 before handoff
- **Linus (Quant):** Agent instructions unaffected — context format identical; zero downstream impact
- **Session log:** `.squad/log/2026-03-28T1347-cosmosdb-refactor-implementation.md`

**Status:** ✅ Architecture delivered and fully implemented

## Learnings

**2025-03-29: Domain Entity Rename (decision→activity, signal→alert)**
- Completed exhaustive rename of two core domain concepts across agent instruction files and README
- Changed "decision" → "activity" to reflect that these are agent outputs/actions, not decisions
- Changed "signal" → "alert" to clarify these are actionable notifications, not trading signals
- Updated JSON schema fields in all 4 instruction files (tv_covered_call, tv_cash_secured_put, tv_open_call, tv_open_put)
- Updated all prose, examples, section headers, and documentation in README.md
- Preserved context-specific uses: "FDA decision", "regulatory decision", "technical signals" remain unchanged
- Used systematic sed replacements to ensure consistency across ~800+ lines of instruction text
- Verified zero remaining incorrect references in owned files

### 2026-03-31: Deep Feature Analysis — DGI + Options Strategy

**Context:** Full codebase audit to map current capabilities and propose DGI-specific features.

**Current Architecture (Key Files):**
- `src/agent_runner.py` (500+ lines) — ChatAgent execution, JSON/summary extraction, activity/alert persistence, telemetry
- `src/cosmos_db.py` (800+ lines) — CosmosDB service: symbols, positions, activities, alerts, telemetry, settings
- `src/tv_data_fetcher.py` (1130 lines) — Hybrid BS4 + Playwright: overview, technicals, forecast, dividends, options chain
- `src/context.py` — Activity history injection into agent prompts (last N activities per symbol)
- `src/main.py` — Cron-based scheduler, sequential agent execution (CC → CSP → OpenCall → OpenPut)
- `web/app.py` (1608 lines) — FastAPI: REST APIs, dashboard, symbol CRUD, positions, chat, settings, triggers
- 4 agent wrappers: covered_call, cash_secured_put, open_call_monitor, open_put_monitor
- 4 instruction files: ~12-18KB each, comprehensive analysis frameworks
- 14 HTML templates: dashboard, symbols, symbol_detail, activity/alert detail, chat, settings (3 tabs), fetch_preview

**Key Observations for Feature Planning:**
- Data model is symbol-centric with partition key = ticker; doc_types: symbol_config, activity, alert
- Positions embedded in symbol_config with lifecycle: active → closed; supports roll traceability
- No premium/income tracking — positions store strike/expiration but not premium collected
- No dividend tracking beyond what TradingView provides (ex-div dates, yield)
- No portfolio-level aggregation views (total premium, total dividend income, sector exposure)
- No historical P&L or position outcome tracking (did assignment happen? net result?)
- Chat exists (global + per-symbol) but has no persistent memory or strategy awareness
- Agents run sequentially per type — no cross-agent coordination
- TradingView fetcher gets 5 data types; options chain uses Playwright (slow ~15s per symbol)
- User preference: symbol-centric everything, only global = cron; prefers comprehensive docs

**Deliverable:** Feature proposal report (see task output below)

### TradingView Anti-Bot Monitoring and Configuration (2026-04-01)
- TradingView 403 bot detection blocking has been addressed with comprehensive anti-bot measures (Linus implementation)
- **Deployment consideration:** Rate limiting default (1-3s per request) means fetch times increase 5-15s per symbol
- **Current cron schedule (every 4h):** Should remain sufficient; adjust if batch fetch times exceed 15-20 minutes
- **Monitoring action items:**
  - Track 403 errors in logs to measure effectiveness of anti-bot implementation
  - If 403s persist, increase `tradingview.request_delay_max` to 5-10 seconds in config.yaml
  - Monitor successful fetch rates to ensure no performance degradation
- **Configuration reference:** `config.yaml` → `tradingview.request_delay_min/max` settings
- **Documentation:** See TRADINGVIEW_ANTI_BOT.md for full technical details and troubleshooting
