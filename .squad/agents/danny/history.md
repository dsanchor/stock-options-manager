# Danny — History

## Project Context
- **Project:** options-agent
- **User:** dsanchor
- **Stack:** Python, Microsoft Agent Framework, Azure Foundry (gpt-5.1)
- **MCP:** iflow-mcp_ferdousbhai_investor-agent 1.6.3
- **Description:** Two periodic trading agents for covered call and cash-secured put sell signals. Local runtime, configurable polling, stock symbols from files, decision logs, sell signal alerts.

## Learnings

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
