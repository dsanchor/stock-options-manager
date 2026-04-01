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

### CosmosDB Unified Container Migration (2026-04-01)
- **Migration script:** `scripts/migrate_cosmos_events.py` — 4-phase migration from dual doc_type (activity/alert) to unified is_alert model
- **Phase 1 (Export):** Queries all activities and alerts, writes timestamped JSON backup with integrity validation (count checks)
- **Phase 2 (Transform):** Merges alert docs into parent activities by activity_id, strips dec_/sig_ prefixes, handles orphaned alerts (converts to standalone), resolves duplicate timestamp collisions (appends sequence number)
- **Phase 3 (Write):** Deletes old documents, writes merged unified events to single container, validates write count
- **Phase 4 (Validate):** Count checks (activities + alerts before = events after), spot-checks merged records, verifies no doc_type='alert' or dec_/sig_ IDs remain
- **Script features:** `--dry-run` (phases 1-2 only, reports what would happen), `--restore BACKUP_FILE` (reads backup and restores), progress logging, defensive error handling with clear messages
- **Edge cases handled:** Orphaned alerts (activity_id points to missing activity) → convert to standalone activity with is_alert=true; duplicate timestamps → append _2, _3 sequence; activities already marked is_alert=true → preserve as-is
- **Key file paths:** `scripts/migrate_cosmos_events.py`, `scripts/MIGRATION_RUNBOOK.md`, `backups/*.json` (created on export)
- **Design source:** Danny's `.squad/decisions/inbox/danny-cosmosdb-migration.md` (9-section spec with transformation rules, edge cases, rollback procedure)
- **Testing patterns:** Dry-run first, backup-before-change, restore capability with confirmation, progressive validation, clear error messages with rollback instructions

## Cross-Agent Impact

### Phase 4a Integration with Phases 1–3 (2026-03-28)
- **Rusty (Agent Dev):** Phases 1–3 (service layer, scheduler, web dashboard) provide CosmosDBService API contract
- **Danny (Lead):** Architecture specification (8 sections) fully implemented: Rusty covered phases 1–3, Basher covered phases 4a provisioning/deployment
- **Orchestration log:** See `.squad/orchestration-log/2026-03-28T1350-basher-phase4a.md`

### CosmosDB Migration (2026-04-01)
- **Danny (Lead):** Authored migration design with 4-phase strategy, edge case handling, rollback procedures
- **Basher (Tester):** Implemented migration script per Danny's spec with dry-run, restore, and validation phases
- **Next steps:** Rusty must update `cosmos_db.py`, `agent_runner.py`, `web/app.py` to use new unified model (write_activity with is_alert flag, remove write_alert method, update queries from doc_type='alert' to is_alert=true)

## Orchestration Session (2026-04-01T21:39:57Z)

**Session:** CosmosDB Unified Schema — Decision Consolidation and Team Orchestration

**Status:** Migration script implemented and documented. Ready for dry-run and production execution.

**Team Coordination Update:**
- Danny: Migration design complete with 4-phase strategy, transformation rules, edge case handling
- Rusty: cosmos_db.py implementation complete with backwards compatibility
- Linus: agent_runner.py refactoring complete for unified write path
- Basher (this work): Migration script complete with defensive testing practices

**Pre-Production Execution Checklist:**
1. [Pending] Run `python scripts/migrate_cosmos_events.py --dry-run` against production database
2. [Pending] Review transformation summary for:
   - Unexpected orphaned alerts (should be rare)
   - ID collisions (should be zero)
   - Merge counts align with expectations
3. [Pending] Verify backup file integrity (count matches query results)
4. [Pending] Test `--restore BACKUP_FILE` in non-production environment
5. [Pending] Confirm all validation checks pass (Phase 4)
6. [Pending] Schedule downtime window (2-5 min)
7. [Pending] Execute: Stop app → run migration → validate → restart app
8. [Pending] Smoke test: Trigger one agent run, verify new ID format
9. [Pending] Delete backup after 7 days

**Migration Command Reference:**
```bash
# Dry-run (no database changes, shows transformation summary)
python scripts/migrate_cosmos_events.py --dry-run

# Actual migration (with backup created automatically)
python scripts/migrate_cosmos_events.py

# Rollback if needed (requires explicit 'YES' confirmation)
python scripts/migrate_cosmos_events.py --restore backups/YYYYMMDDTHHMM.json
```

**Session Log:** `.squad/log/2026-04-01T21-39-cosmosdb-unified-schema.md`  
**Orchestration Log:** `.squad/orchestration-log/2026-04-01T21-39-basher.md`

