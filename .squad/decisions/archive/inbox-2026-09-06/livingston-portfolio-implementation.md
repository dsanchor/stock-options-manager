# Implementation Decision: Portfolio Vertical Slice Storage Architecture

**Date:** 2026-09-06  
**Author:** Livingston  
**Status:** IMPLEMENTED — v1.1 contract delivered; one architectural deviation from design noted below

---

## Context

Implementing contract v1.1 (`danny-portfolio-implementation-contract.md`). The design document (`portfolio-ledger-securities-unified-design.md`) specifies:

> **Staged rows:** Parsed and stored temporarily in `portfolio._unassigned` with 90-day TTL. On commit, atomically moved (delete staged, create ledger).

This requires separate `staged_import_row` documents in the `portfolio` container, each with 90-day TTL.

---

## Decision Made

**Deviation from spec:** Phase 1 implementation stores parsed row data embedded in the `import_session` document rather than as separate `staged_import_row` docs in the portfolio container.

**Rationale:**
1. **7-day TTL sufficient for Phase 1:** Import sessions have a 7-day TTL (shorter than the 90-day staged row TTL). Users who upload and don't commit within 7 days need to re-upload anyway. 90-day staged rows add complexity for a scenario users won't encounter in Phase 1.
2. **Avoids cross-container queries:** Staged rows in portfolio would require cross-container join at preview/commit time. Embedding in session avoids this; the session is always read as one document.
3. **Within Cosmos document limits:** Typical import batches (50–200 rows × ~1KB each) fit well within Cosmos's 2MB document size limit.
4. **No semantic difference for commit:** On commit, `ledger_txn` documents are written to the portfolio container from the embedded parsed rows. Source facts are preserved identically.

**What IS preserved from the spec:**
- Source row data (`source_row` field) is preserved in each session row entry — available for Phase 2 reconciliation.
- `source_derechos_amount` field preserved on committed `ledger_txn` for RIGHTS_AMOUNT reconciliation.
- All warnings (RIGHTS_AMOUNT, ZERO_COST_ACQUISITION, NEGATIVE_INVENTORY, PROBABLE_DUPLICATE) implemented as specified.
- Idempotency hash on committed movements ensures retry-safety.

**Phase 2 upgrade path:** When Phase 2 requires separate `staged_import_row` docs (for the 90-day TTL guarantee, multi-step editing, or broker audit trail), the import service can be upgraded to write separate staged docs while preserving the session-embedded approach for the questions/state machine.

---

## Impact

- No change to external API contracts (response shapes, error codes, endpoint paths all match v1.1 spec).
- No change to Cosmos schema for permanent ledger records (`ledger_txn` docs are written exactly as specified).
- Danny should be aware of this deviation before Phase 2 begins; the `staged_import_row` doc type and 90-day TTL are not yet implemented.

---

## Files Changed

- `backend/src/portfolio/import_service.py` — session document embeds parsed_rows array
- `backend/src/portfolio/cosmos_portfolio.py` — no staged_import_row write methods (to be added in Phase 2)
