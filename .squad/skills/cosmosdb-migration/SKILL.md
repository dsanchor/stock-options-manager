---
name: "cosmosdb-migration"
description: "Pattern for safe CosmosDB schema migrations with backup, validation, and rollback"
domain: "data-migration"
confidence: "high"
source: "earned"
---

## Context
When migrating CosmosDB document schemas (renaming fields, merging doc types, changing ID formats) in a low-traffic serverless environment. Applies when data volume is small enough for in-memory processing (<10K docs).

## Patterns

1. **Offline batch with backup-first**: Export all affected documents to JSON before any writes. Migration window is acceptable when traffic is low.

2. **Four-phase script structure**:
   - Pre-flight: count, validate references, log baseline
   - Transform: build new documents in memory (no writes yet)
   - Write: create new docs, then delete old docs (create-before-delete for safety)
   - Post-flight: count validation, spot-checks, no-stale-format assertions

3. **Idempotent operations**: Check if target ID exists before creating; check if source ID exists before deleting. Script can be re-run safely after partial failure.

4. **Dry-run flag**: `--dry-run` prints all transformations without writing. Always test against production data before executing.

5. **ID migration via create+delete (not update)**: CosmosDB does not support changing document IDs. Create new doc with new ID, delete old doc. Both in same partition for consistency.

6. **Reference fixup**: When merging document types, inline the referenced data into the parent document rather than maintaining cross-references.

## Examples

```python
# Safe ID migration pattern
new_doc = {**old_doc, "id": new_id}
container.create_item(new_doc)           # create first
container.delete_item(old_id, pk)         # delete after

# Idempotent check
try:
    container.read_item(new_id, pk)
    log.info(f"Already migrated: {new_id}")
except CosmosResourceNotFoundError:
    container.create_item(new_doc)
```

## Anti-Patterns

- **In-place ID changes**: CosmosDB IDs are immutable. Must create new + delete old.
- **Delete-before-create**: Risk of data loss on failure. Always create the new document first.
- **No backup**: Never run destructive migration without JSON export. Even with "small" data.
- **Zero-downtime for low-traffic apps**: Adds code complexity (dual-write, feature flags) for no real benefit when downtime is 2-5 minutes.
