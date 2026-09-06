# Portfolio Phase 2 — Revision Contract

**Author:** Linus (independent revision author)  
**Date:** 2026-09-06  
**Status:** IMPLEMENTED — supersedes defective Livingston implementation  
**For:** Rusty (frontend consumer), Basher (test validator)  
**Predecessor:** `livingston-phase2-api-contract.md` (API shape unchanged; defects corrected)

---

## Summary of Changes from Livingston's Implementation

The API surface is **unchanged** from `livingston-phase2-api-contract.md`. All endpoint
paths, request/response shapes, and HTTP status codes remain as specified. The following
behavioral corrections were applied:

---

## Defect Corrections

### D1 — `total_purchases_eur` is now BUY-only

**Affected:** `GET /api/portfolio/holdings` response (per-holding and summary).

`total_purchases_eur` now counts only actual BUY acquisition outflows (gross + fees).
TRANSFER_IN carried cost basis is excluded from purchases.

| Field | Includes BUY | Includes TRANSFER_IN | Now correct |
|-------|:---:|:---:|:---:|
| `total_purchases_eur` | ✅ | ❌ | ✅ fixed |
| `total_invested_eur` | ✅ | ✅ | ✅ unchanged |
| `avg_cost_basis_eur` | ✅ | ✅ (carried shares) | ✅ unchanged |
| `current_invested_eur` | uses `total_invested - total_sales` | — | ✅ semantically fixed |

**Observable example:**
- BUY 100 @ €1,000 + TRANSFER_IN 50 shares (basis €500):
  - `total_purchases_eur = "1000.00"` (not 1500)
  - `total_invested_eur = "1500.00"`
- TRANSFER_IN only, no BUY:
  - `total_purchases_eur = "0.00"`
  - `total_invested_eur = "500.00"`

### D2 — Individual reassignment requires non-blank `reason`

**Affected:** `POST /api/portfolio/movements/{movement_id}/reassign`

Missing, empty (`""`), or whitespace-only `reason` now returns:
```json
// HTTP 400
{ "error": "validation_error", "detail": "reason is required" }
```

### D3 — Batch reassignment requires non-blank `reason`

**Affected:** `POST /api/portfolio/movements/batch-reassign`

Same validation as D2. Missing/blank/whitespace `reason` → 400.

### D4 — Batch reassignment is now all-or-nothing

**Affected:** `POST /api/portfolio/movements/batch-reassign`

**Atomicity contract:**
- If all candidates succeed → normal 200 response with `reassigned_count` and `ids`.
- `skipped_count` is always `0` (fail-fast; no silent skips).
- On any candidate failure → compensating rollback is performed for all prior
  reassignments in this batch, then:

```json
// HTTP 500
{
  "error": "batch_reassign_failed",
  "detail": "batch_reassign_failed: operation rolled back after failure on movement 'mvt_...'. N prior reassignment(s) were reversed. Cause: ..."
}
```

**Rollback semantics:**
1. Delete new document from destination partition.
2. Restore original to `correction_status = "ACTIVE"` in source partition.
3. Best-effort per step — rollback failures are logged as `MANUAL CLEANUP REQUIRED`
   but do not suppress the 500 response (caller always knows the operation failed).

**Stale preview is acceptable:** The execution endpoint re-derives candidates at
execution time. Between preview and execution, the set may change; this is expected.

---

## Preview Endpoint — Validated and Accepted

`POST /api/portfolio/movements/batch-reassign/preview`

Livingston's partial implementation was reviewed and accepted as-is:
- Uses `_fetch_reassign_candidates()` — the same predicate as execution. ✅
- Read-only: no Cosmos writes. ✅
- Returns specified shape. ✅
- Route validates source ≠ dest and both required. ✅

Response shape:
```json
{
  "affected_count": 12,
  "movement_ids": ["mvt_001", "mvt_002", ...],
  "sample": [
    {
      "id": "mvt_001",
      "security_id": "XNYS:AAPL",
      "txn_type": "BUY",
      "trade_date": "2026-01-15",
      "quantity": "100",
      "account_id": "_unassigned"
    }
  ],
  "source_account_id": "...",
  "dest_account_id": "..."
}
```

`sample` is bounded to first 10 candidates.

---

## Invariants for Rusty (Frontend)

1. **Do not send `affected_count` from preview back to execution.** The server ignores
   any client-supplied count. Execution always re-derives candidates server-side.
2. A stale preview is normal — the candidate set may shift between preview and execute.
   Show the execution's `reassigned_count` as the authoritative result.
3. On 500 with `error: "batch_reassign_failed"`, all movements were rolled back.
   Display the error; do not show any partial success count.
4. `total_purchases_eur` now correctly excludes transfer-in basis. If your UI shows
   this field, no contract change needed — it was wrong before, now correct.

---

## Invariants for Basher (Tests)

All four strict xfail markers have been removed. Their tests now pass:

| Test | Was | Now |
|------|-----|-----|
| `test_reassign_missing_reason_400` | xfail | ✅ PASS |
| `test_batch_missing_reason_400` | xfail | ✅ PASS |
| `test_batch_is_atomic_on_failure` | xfail | ✅ PASS |
| `test_transfer_excluded_from_purchases_eur` | xfail | ✅ PASS |

Full portfolio test suite: **505 passed, 0 failed**.

`conftest_portfolio_p2.py::FakePortfolioContainer` now supports `delete_item`
(required by `_rollback_batch_reassign`).

---

## Files Changed

| File | Change |
|------|--------|
| `backend/src/portfolio/holdings_service.py` | D1: `total_buy_cost_eur` accumulator; separate `total_purchases_eur` from `total_invested_eur` |
| `backend/web/portfolio_routes.py` | D2/D3: reason validation; D4: `batch_reassign_failed` → 500 |
| `backend/src/portfolio/cosmos_portfolio.py` | D4: fail-fast + `_rollback_batch_reassign` |
| `backend/tests/conftest_portfolio_p2.py` | `delete_item` on `FakePortfolioContainer` |
| `backend/tests/test_portfolio_phase2_reassignment.py` | Remove 3 xfail markers |
| `backend/tests/test_portfolio_phase2_transfers.py` | Remove 1 xfail marker |
