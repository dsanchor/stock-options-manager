# Basher — History

## Project Context
- **Project:** options-agent
- **User:** dsanchor
- **Stack:** Python, Microsoft Agent Framework, Azure Foundry (gpt-5.4-mini)
- **MCP:** iflow-mcp_ferdousbhai_investor-agent 1.6.3
- **Description:** Two periodic trading agents for covered call and cash-secured put sell signals. Local runtime, configurable polling, stock symbols from files, decision logs, sell signal alerts.

## Learnings

### Phase 4a — Provisioning, Dockerfile, README (2026-03-28)
- **Architecture:** CosmosDB single-container, partition by `/symbol`, three doc types: `symbol_config`, `decision`, `signal`
- **Indexing:** Custom policy indexes only query fields (`symbol`, `doc_type`, `timestamp`, `watchlist/*`, `agent_type`, `decision`); excludes large blobs (`reason`, `raw_response`, `analysis_context`)
- **Provisioning:** `scripts/provision_cosmosdb.sh` — idempotent, serverless default, customizable via env vars
- **Migration:** `scripts/migrate_to_cosmosdb.py` — idempotent (catches `CosmosResourceExistsError`), reads from `data/*.txt` + `logs/*.jsonl`, imports `src.cosmos_db.CosmosDBService`
- **Dockerfile:** Removed `data/` and `logs/` volume mounts, added `scripts/` copy — no persistent local storage needed
- **README:** Updated architecture description, env vars table, Docker run examples, added CosmosDB Setup + Migration + Environment Variables sections
- **Key file paths:** `scripts/provision_cosmosdb.sh`, `scripts/migrate_to_cosmosdb.py`, `Dockerfile`, `README.md`
- **Dependency:** Migration script imports `src.cosmos_db.CosmosDBService` (created by Rusty in Phase 1)

## Cross-Agent Impact

### Phase 4a Integration with Phases 1–3 (2026-03-28)
- **Rusty (Agent Dev):** Phases 1–3 (service layer, scheduler, web dashboard) provide CosmosDBService API contract
- **Danny (Lead):** Architecture specification (8 sections) fully implemented: Rusty covered phases 1–3, Basher covered phases 4a provisioning/deployment
- **Orchestration log:** See `.squad/orchestration-log/2026-03-28T1350-basher-phase4a.md`
