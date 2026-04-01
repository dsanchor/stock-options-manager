# CosmosDB Unified Container Migration Runbook

**Script:** `scripts/migrate_cosmos_events.py`  
**Design:** `.squad/decisions/inbox/danny-cosmosdb-migration.md`  
**Testing Strategy:** `.squad/decisions/inbox/basher-migration-testing.md`

---

## Pre-Migration Checklist

- [ ] Read Danny's migration design document
- [ ] Ensure CosmosDB credentials are set (`COSMOS_ENDPOINT`, `COSMOS_KEY`)
- [ ] Stop all agent runs (no writes during migration)
- [ ] Backup current database state

---

## Execution Steps

### Step 1: Dry-Run (Required)
```bash
python scripts/migrate_cosmos_events.py --dry-run
```

Review output:
- Activity and alert counts
- Number of orphaned alerts (should be 0 or very low)
- ID collisions (should be 0)
- Transformation summary

### Step 2: Review Backup
The dry-run creates a backup in `backups/backup_YYYYMMDDTHHMM.json`. Verify:
```bash
ls -lh backups/
# Check file size is reasonable (activities + alerts JSON)
```

### Step 3: Execute Migration
If dry-run output looks correct:
```bash
python scripts/migrate_cosmos_events.py
```

This will:
1. Export backup (Phase 1)
2. Merge and transform (Phase 2)
3. Write unified events (Phase 3)
4. Validate (Phase 4)

### Step 4: Smoke Test
After migration completes:
1. Restart the application
2. Trigger a single agent run
3. Verify new activity ID format (no `dec_` prefix)
4. Check web dashboard displays activities/alerts correctly

---

## Rollback Procedure

If migration fails or validation errors occur:

```bash
# Stop the application
python scripts/migrate_cosmos_events.py --restore backups/backup_YYYYMMDDTHHMM.json
# Type 'YES' when prompted
# Restart the application
```

**Note:** Restore deletes all current activity/alert documents and replaces with backup.

---

## Expected Output

### Dry-Run Output
```
======================================================================
CosmosDB Unified Container Migration
======================================================================
🔍 DRY-RUN MODE: No writes will be performed

📦 PHASE 1: Export Backup
Querying activities...
Found X activity documents
Querying alerts...
Found Y alert documents
Backup validated: X activities + Y alerts
✅ Backup created: backups/backup_20260401T1430.json

🔧 PHASE 2: Merge and Transform
Merging alerts into parent activities...
Merged alert sig_AAPL_covered_call_20260328T14_3000 into activity dec_AAPL_covered_call_20260328T14_3000
Transforming activity IDs...
Transformed Z documents
✅ Transformation complete

----------------------------------------------------------------------
TRANSFORMATION SUMMARY
----------------------------------------------------------------------
Activities before:     X
Alerts before:         Y
Alerts merged:         Y
Alerts orphaned:       0
ID collisions:         0
Documents after:       X
----------------------------------------------------------------------

🔍 DRY-RUN COMPLETE: Phases 3-4 skipped
Backup file saved: backups/backup_20260401T1430.json
Review the transformation summary above.
To execute the migration, run without --dry-run flag.
```

### Full Migration Output
(Dry-run output plus):
```
✍️  PHASE 3: Write Unified Events
Writing X unified documents...
Written X documents
Deleting old activity documents...
Deleting old alert documents...
Deleted Y old documents
✅ Written X unified documents

✓ PHASE 4: Validate
Running validation checks...
✓ Activity count: X (expected X)
✓ Alert count: Y (expected Y)
✓ No doc_type='alert' documents remain
✓ No dec_/sig_ prefixed IDs remain
✓ Spot-checked 3 merged alert records
All validation checks passed
✅ Validation passed

======================================================================
🎉 MIGRATION COMPLETE
======================================================================

FINAL SUMMARY
----------------------------------------------------------------------
Total documents migrated:  X
Alerts merged:             Y
Orphaned alerts:           0
ID collisions resolved:    0
----------------------------------------------------------------------
Backup file: backups/backup_20260401T1430.json
Keep backup for 7 days in case rollback is needed.
```

---

## Edge Cases Handled

| Scenario | Handling |
|----------|----------|
| Orphaned alert (no parent activity) | Converts to standalone activity with `is_alert=true`, strips `sig_` prefix, logs warning |
| Duplicate timestamp (ID collision) | Appends `_2`, `_3` sequence number, logs collision |
| Missing symbol field | Logs warning, skips delete (safe failure) |
| Document already exists (idempotent) | Logs warning, skips create |
| CosmosDB rate limit | Sequential writes with retry (low volume: 50-100 docs) |

---

## Validation Checks (Phase 4)

✓ Activity count matches expected (activities before)  
✓ Alert count matches merged count (alerts before)  
✓ No `doc_type='alert'` documents remain  
✓ No `dec_` or `sig_` prefixed IDs remain  
✓ Spot-check 3 random merged records for correctness  

---

## Troubleshooting

### Error: "Missing required environment variables"
Set CosmosDB credentials:
```bash
export COSMOS_ENDPOINT="https://your-account.documents.azure.com:443/"
export COSMOS_KEY="your-key-here"
```

### Error: "Activity count mismatch"
Migration failed during Phase 3. Run restore:
```bash
python scripts/migrate_cosmos_events.py --restore backups/backup_YYYYMMDDTHHMM.json
```

### Error: "Backup file not found"
Check `backups/` directory. If missing, re-run dry-run to create backup.

### High orphaned alerts count
Review orphaned alerts in transformation summary. These are alerts with `activity_id` pointing to missing activities (may be TTL-expired). Script converts them to standalone activities.

---

## Post-Migration Tasks

After successful migration:
1. Update `cosmos_db.py` to use new ID format (remove `dec_` prefix in `write_activity()`)
2. Update `agent_runner.py` to use `mark_as_alert()` instead of `write_alert()`
3. Update `web/app.py` queries to use `is_alert=true` instead of `doc_type='alert'`
4. Test all dashboard endpoints
5. Keep backup file for 7 days, then delete

---

## Timeline

- **Downtime window:** 2-5 minutes
- **Script execution:** ~1-2 minutes (50-100 documents)
- **Smoke test:** ~5 minutes

**Total:** ~10 minutes end-to-end
