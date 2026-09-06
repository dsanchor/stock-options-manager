# Phase 2 Backend Rejection — Retrospective & Revision Contract for Linus

**Date:** 2026-09-06  
**Author:** Danny (Lead / Retrospective Facilitator)  
**Status:** FROZEN — authoritative revision specification  
**Trigger:** REJECT of Livingston's Phase 2 backend implementation by Basher (4 confirmed defects) + Rusty gap flag (1 missing endpoint)  
**Predecessor decisions:**  
- `copilot-directive-20260906-phase2-portfolio.md` (accepted scope)  
- `livingston-phase2-api-contract.md` (accepted API contract)  
- `livingston-phase2-implementation-decisions.md` (Decision 7: TRANSFER_IN not a purchase)  
- `rusty-gap-batch-reassign-preview.md` (missing preview endpoint)  

---

## Rejection Lockout

| Agent | Role | Status |
|-------|------|--------|
| **Livingston** | Original Phase 2 backend author | ❌ Locked out — must not revise, advise, or contribute |
| **Danny** | Reviewer / Lead | ⛔ Does not implement (reviewer separation) |
| **Basher** | Tester / Reviewer | ⛔ Does not implement (charter restriction) |
| **Rusty** | Frontend consumer | Not locked out, but not assigned backend work |

### Revision Assignment

**All backend fixes in this document are assigned to Linus** as an independent revision author with no prior involvement in the Phase 2 backend implementation.

---

## Retrospective: Root Cause Analysis

### Why four defects shipped together

**Common root cause: insufficient separation of cost-model concepts and missing validation gates.**

1. **Defect 1 (TRANSFER_IN inflates `total_purchases_eur`):** The holdings aggregation uses a single `total_cost_eur` accumulator for both purchases and carried basis. Livingston's own Decision 7 explicitly states "TRANSFER_IN does NOT count toward `total_purchases_eur`" — but the implementation then aliases `total_purchases_eur = total_cost_eur` at output time, collapsing the distinction. This is a design-intent-vs-implementation gap: the decision was correct, the code ignored it.

2. **Defects 2 & 3 (blank reason accepted):** The route handlers default `reason` to `str(body.get("reason", ""))`, which silently converts missing/null/blank into an empty string. No validation gate rejects it. The contract shows `reason` as a required field in examples but the routes treat it as optional. Classic missing-validation pattern.

3. **Defect 4 (batch reassignment non-atomic):** The batch implementation iterates candidates with per-item try/except, incrementing `skipped_count` on failures. This produces silent partial application — the exact opposite of the accepted requirement "no silent bulk action." Livingston's Decision 5 acknowledged Cosmos's lack of cross-partition transactions but chose the "write-then-mark" protocol for individual reassignment without addressing how to make the batch variant all-or-nothing.

4. **Gap (missing preview endpoint):** The accepted UX requirement ("include a preview/confirmation of affected count; no silent bulk action") was not implemented. Livingston partially addressed this — the current diff shows `preview_batch_reassign` and its route were added, but the defects in the execution path overshadow it.

### Pattern: decisions.md says X, implementation does Y

This is the third rejection cycle where the implementation diverges from a documented decision. Prior cycles: F1 fees hardcoded to zero despite contract specifying `fees.total`, F7 avg_cost dividing by transactions despite "per share" intent. The pattern is: the author writes the correct design intent, then implements a simpler version that loses a key invariant.

**Structural recommendation (carried to learnings):** From now on, every design decision that introduces a numeric invariant must include an explicit "test assertion" line — the exact comparison a test should make. This forces the author to think about the output value, not just the code path.

---

## Defect 1: TRANSFER_IN Carried Basis Inflates `total_purchases_eur`

### Problem

`holdings_service.py` uses `total_cost_eur` to accumulate both BUY acquisition costs and TRANSFER_IN carried basis. At output time:

```python
"total_purchases_eur": str(total_cost.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)),
```

This aliases `total_purchases_eur = total_cost_eur`, so transferred shares appear as if they were purchased, double-counting in cross-account views.

Livingston's own Decision 7 states:  
> "TRANSFER_IN does NOT count toward `total_purchases_eur` — it is not a purchase."

### Authoritative Semantic Definitions

| Field | Definition | Includes BUY? | Includes TRANSFER_IN? | Includes TRANSFER_OUT? |
|-------|-----------|:---:|:---:|:---:|
| `total_cost_eur` (internal) | Total acquisition cost basis in this account | ✅ gross+fees | ✅ carried basis | ❌ subtracted |
| `total_purchases_eur` (output) | Money actually spent buying securities | ✅ gross+fees | ❌ | ❌ |
| `total_invested_eur` (output) | Total cost basis (purchases + carried transfers) | ✅ | ✅ | ❌ subtracted |
| `avg_cost_basis_eur` (output) | Per-share cost = total_cost / paid_buy_shares | Uses total_cost_eur | Includes carried shares | — |

### Required Fix

Add a separate `total_buy_cost_eur` accumulator that tracks only BUY gross+fees. Use it for `total_purchases_eur`. Keep `total_cost_eur` for `total_invested_eur` and `avg_cost_basis_eur`.

**In `holdings_service.py` per-security initialization:**

```python
per_security[security_id] = {
    ...
    "total_cost_eur": Decimal("0"),       # all acquisition cost (BUY + TRANSFER_IN)
    "total_buy_cost_eur": Decimal("0"),   # BUY-only cost (gross + fees)
    ...
}
```

**In the BUY branch:**

```python
if txn_type == "BUY":
    agg["total_shares"] += qty
    if cost_basis_status != "INCOMPLETE":
        cost = gross_eur + commission_eur
        agg["total_cost_eur"] += cost
        agg["total_buy_cost_eur"] += cost
        agg["paid_buy_shares"] += qty
    else:
        agg["zero_cost_count"] += 1
    agg["buy_count"] += 1
```

**TRANSFER_IN branch — unchanged** (adds to `total_cost_eur` only, NOT to `total_buy_cost_eur`).

**At output time:**

```python
buy_cost = agg["total_buy_cost_eur"]
total_cost = agg["total_cost_eur"]

# Per-holding:
"total_invested_eur": str(total_cost.quantize(...)),
"total_purchases_eur": str(buy_cost.quantize(...)),

# Summary accumulators:
total_purchases += buy_cost        # only real purchases
total_invested += total_cost       # purchases + carried basis
```

### Required Tests

| Test | Assertion |
|------|-----------|
| `test_transfer_in_not_in_total_purchases` | BUY 100@€1000 + TRANSFER_IN 50 (basis €500) → `total_purchases_eur = "1000.00"`, `total_invested_eur = "1500.00"` |
| `test_transfer_only_holding_zero_purchases` | TRANSFER_IN 50 (basis €500) only → `total_purchases_eur = "0.00"`, `total_invested_eur = "500.00"` |
| `test_summary_separates_purchases_from_transfers` | Same as above but check the summary-level `total_purchases_eur` and `total_invested_eur` |

### Basher xfail to convert

Remove `xfail` from the existing test that flags this defect. The test should now pass.

---

## Defect 2: Individual Reassignment Accepts Blank Reason

### Problem

`portfolio_routes.py` line in `reassign_movement`:

```python
reason=str(body.get("reason", "")),
```

Passes empty string to the service. No validation rejects blank/missing/whitespace-only.

### Required Fix

Add validation in the route handler, before calling the service:

```python
reason = str(body.get("reason", "")).strip()
if not reason:
    return _err("validation_error", "reason is required", 400)
```

### Required Tests

| Test | Assertion |
|------|-----------|
| `test_individual_reassign_blank_reason_400` | `reason: ""` → 400 |
| `test_individual_reassign_missing_reason_400` | no `reason` field → 400 |
| `test_individual_reassign_whitespace_reason_400` | `reason: "   "` → 400 |
| `test_individual_reassign_valid_reason_accepted` | `reason: "Wrong account"` → 200 |

### Basher xfail to convert

Remove `xfail` from the existing test that flags this defect.

---

## Defect 3: Batch Reassignment Accepts Blank Reason

### Problem

Same pattern as Defect 2 in `batch_reassign_movements` route handler.

### Required Fix

Same validation pattern:

```python
reason = str(body.get("reason", "")).strip()
if not reason:
    return _err("validation_error", "reason is required", 400)
```

### Required Tests

| Test | Assertion |
|------|-----------|
| `test_batch_reassign_blank_reason_400` | `reason: ""` → 400 |
| `test_batch_reassign_missing_reason_400` | no `reason` field → 400 |
| `test_batch_reassign_valid_reason_accepted` | `reason: "Bulk correction"` → 200 |

### Basher xfail to convert

Remove `xfail` from the existing test that flags this defect.

---

## Defect 4: Batch Reassignment Non-Atomic (Partial Apply)

### Problem

`cosmos_portfolio.py` `batch_reassign_movements()`:

```python
for doc in candidates:
    try:
        result = self.reassign_movement(...)
        reassigned.append(result["new_id"])
    except Exception as exc:
        logger.warning("batch_reassign: skipping %s: %s", doc.get("id"), exc)
        skipped += 1
```

This silently skips failures, producing partial results. The accepted requirement is "no silent bulk action."

### Design Decision: Staged-Operation Pattern

**Cosmos constraint:** Transactional batch in Cosmos DB is limited to a single logical partition key. Reassignment by definition moves documents across partitions (source → dest). A single atomic transaction is physically impossible.

**Chosen design: Fail-fast with compensating rollback.**

The operation must be all-or-nothing from the caller's perspective:

1. **Phase 1 — Candidate selection:** Query all matching active movements from the source partition (same `_fetch_reassign_candidates` predicate as preview).

2. **Phase 2 — Sequential execution with rollback on first failure:**
   - For each candidate, call `reassign_movement()` (write to dest, mark source SUPERSEDED).
   - On the **first failure**, stop processing remaining candidates.
   - **Compensating rollback:** For all already-reassigned movements in this batch, reverse them:
     - Delete the new doc from the dest partition.
     - Un-SUPERSEDE the original (restore `correction_status = "ACTIVE"`, remove `superseded_by`).
   - Return a 500 error with the specific failure detail, not a partial success.

3. **Phase 3 — Success:** If all candidates succeed, return the normal response.

**Rollback semantics:**
- Best-effort: if a rollback step itself fails, log the specific document IDs for manual cleanup and still return 500. The caller knows the operation failed atomically.
- The original document in the source partition is the source of truth: if it is still ACTIVE (rollback succeeded), the movement was never reassigned. If it is SUPERSEDED but the new doc was deleted (partial rollback), the document needs manual attention — but this is better than silent partial application.

**Alternative considered and rejected: Saga with staged operation document.**  
A more robust approach would write a `batch_reassign_operation` document first, execute steps, mark complete. This is overkill for Phase 2 volumes (max tens of movements) and adds significant complexity. If volumes grow, Phase 3 can upgrade to a saga.

**Alternative considered and rejected: Constraining to same-partition only.**  
This would eliminate the problem but defeats the purpose of reassignment (cross-account = cross-partition by definition).

### Required Fix

Replace the per-item try/except with fail-fast + compensating rollback:

```python
def batch_reassign_movements(self, ...) -> Dict[str, Any]:
    ...
    candidates = self._fetch_reassign_candidates(...)
    
    completed = []  # list of {"original_id", "new_id", "source_account_id"}
    
    for doc in candidates:
        try:
            result = self.reassign_movement(
                movement_id=doc["id"],
                source_account_id=source_account_id,
                dest_account_id=dest_account_id,
                reason=reason,
            )
            completed.append({
                "original_id": doc["id"],
                "new_id": result["new_id"],
                "source_account_id": source_account_id,
            })
        except Exception as exc:
            logger.error(
                "batch_reassign: failed on %s after %d successes, rolling back: %s",
                doc.get("id"), len(completed), exc,
            )
            # Compensating rollback
            self._rollback_batch_reassign(completed, dest_account_id)
            raise ValueError(
                f"batch_reassign_failed: operation rolled back after failure on "
                f"movement {doc.get('id')!r}. {len(completed)} prior reassignment(s) "
                f"were reversed. Cause: {exc}"
            )
    
    return {
        "reassigned_count": len(completed),
        "skipped_count": 0,
        "ids": [c["new_id"] for c in completed],
    }


def _rollback_batch_reassign(
    self, completed: list, dest_account_id: str
) -> None:
    """Best-effort compensating rollback for a failed batch reassignment."""
    for entry in completed:
        try:
            # 1. Delete the new doc from dest partition
            self._portfolio.delete_item(
                item=entry["new_id"],
                partition_key=dest_account_id,
            )
        except Exception as del_exc:
            logger.error(
                "batch_rollback: could not delete new doc %s from %s: %s",
                entry["new_id"], dest_account_id, del_exc,
            )
        
        try:
            # 2. Un-SUPERSEDE the original
            original = self._portfolio.read_item(
                item=entry["original_id"],
                partition_key=entry["source_account_id"],
            )
            original["correction_status"] = "ACTIVE"
            original.pop("superseded_by", None)
            self._portfolio.upsert_item(original)
        except Exception as restore_exc:
            logger.error(
                "batch_rollback: could not restore original %s in %s: %s "
                "(MANUAL CLEANUP REQUIRED)",
                entry["original_id"], entry["source_account_id"], restore_exc,
            )
```

### Route handler change

Catch the `ValueError` from the rolled-back batch and return 500 (not 200 with partial counts):

```python
# In batch_reassign_movements route handler:
except ValueError as exc:
    msg = str(exc)
    if "batch_reassign_failed" in msg:
        return _err("batch_reassign_failed", msg, 500)
    if "same_account" in msg:
        return _err("validation_error", msg, 400)
    return _err("validation_error", msg, 400)
```

### Required Tests

| Test | Assertion |
|------|-----------|
| `test_batch_reassign_all_succeed` | 3 movements → all reassigned, `reassigned_count=3`, `skipped_count=0` |
| `test_batch_reassign_failure_rolls_back` | Inject failure on 2nd of 3 → 1st is rolled back (original restored to ACTIVE, new doc deleted), response is 500 |
| `test_batch_reassign_rollback_restores_originals` | After rollback, all originals are ACTIVE in source partition |
| `test_batch_reassign_no_partial_success_response` | On failure, response never contains `reassigned_count > 0` |

### Basher xfail to convert

Remove `xfail` from the existing test that flags this defect.

---

## Gap: Batch Reassignment Preview Endpoint

### Status

Livingston partially implemented this before lockout — the current diff includes `preview_batch_reassign` in `cosmos_portfolio.py` and the route handler. However, the implementation must be validated against the contract below.

### Design Decisions: Preview ↔ Execution Predicate Sharing

**1. Shared predicate via `_fetch_reassign_candidates()`:**

Both `preview_batch_reassign()` and `batch_reassign_movements()` MUST use the identical `_fetch_reassign_candidates()` method. This is already the case in the current diff — verify it remains true after the defect fixes.

**2. Execution never trusts client counts:**

The execution endpoint re-derives the candidate set at execution time. The client must NOT send the preview count back to execution. The server ignores any client-supplied count or ID list.

**3. Stale preview protection:**

The execution endpoint does NOT validate against a prior preview count. Between preview and execution, the candidate set may change (new movements added, some already reassigned). This is acceptable for Phase 2 — the operation is idempotent per-movement (already-SUPERSEDED movements are naturally skipped by the candidate query which filters `correction_status = 'ACTIVE'`).

**4. Preview response shape:**

```json
{
  "affected_count": 12,
  "movement_ids": ["mvt_001", "mvt_002", ...],
  "sample": [
    {
      "id": "mvt_001",
      "security_id": "XNYS:AAPL",
      "ticker": "AAPL",
      "trade_date": "2026-01-15",
      "txn_type": "BUY",
      "quantity": "100"
    }
  ],
  "source_account_id": "acct_ibkr_main",
  "dest_account_id": "acct_fidelity_main"
}
```

`sample` contains the first 10 candidates with key display fields.

### Required Validation

Linus must verify the existing `preview_batch_reassign` implementation:

1. Uses `_fetch_reassign_candidates()` (shared predicate) — **confirmed in current diff**.
2. Returns the shape above — verify and adjust if needed.
3. Route validation (source ≠ dest, both required) — **confirmed in current diff**.
4. Is read-only (no writes) — verify.

If the existing implementation matches, no changes needed. If it diverges, fix to match.

### Required Tests

| Test | Assertion |
|------|-----------|
| `test_preview_returns_affected_count` | Preview with 3 matching movements → `affected_count: 3` |
| `test_preview_returns_movement_ids` | Preview returns `movement_ids` list matching candidates |
| `test_preview_is_read_only` | After preview, all source movements remain ACTIVE |
| `test_preview_and_execute_share_predicate` | Same filters to preview and execute → same set processed |

---

## Handling Livingston's Partial Preview Edits

The current diff shows `preview_batch_reassign` and its route already implemented. **Linus must not discard this work.** The approach:

1. **Inspect** the existing preview implementation for correctness against the contract above.
2. **Keep** what is correct; **fix** what diverges.
3. **Do not rewrite** from scratch — the method structure and route registration are correct.
4. If the existing code uses `_fetch_reassign_candidates()` correctly and is read-only, it is accepted as-is (modulo the response shape).

---

## File Change Summary

| File | Defect | Change |
|------|--------|--------|
| `backend/src/portfolio/holdings_service.py` | D1 | Add `total_buy_cost_eur` accumulator; separate `total_purchases_eur` from `total_invested_eur` |
| `backend/web/portfolio_routes.py` | D2 | Validate `reason` non-blank in `reassign_movement` |
| `backend/web/portfolio_routes.py` | D3 | Validate `reason` non-blank in `batch_reassign_movements` |
| `backend/web/portfolio_routes.py` | D4 | Catch `batch_reassign_failed` ValueError → 500 |
| `backend/src/portfolio/cosmos_portfolio.py` | D4 | Fail-fast + compensating rollback in `batch_reassign_movements` |
| `backend/src/portfolio/cosmos_portfolio.py` | D4 | Add `_rollback_batch_reassign` method |
| Preview (verify/adjust only) | Gap | Validate existing implementation matches contract |

---

## Correction Sequence

```
1. D2 + D3 — Reason validation  (smallest, route-only, independent)
2. D1 — Purchase vs transfer cost separation  (holdings, independent)
3. D4 — Batch atomicity with rollback  (most complex, service + route)
4. Gap — Verify preview implementation  (read-only, verification)
```

---

## Validation Gate

After all corrections:

1. `cd backend && python -m pytest tests/ -x -v` — all tests pass (existing + new)
2. All four Basher `xfail` markers must be removed and their tests must pass
3. No new `xfail` markers may be introduced
4. `cd frontend && npm run build` — build succeeds (no frontend changes expected, but verify)
5. Basher runs full regression validation
6. Danny re-reviews the corrected diff

---

## Acceptance Criteria for Replacement Author (Linus)

### MUST (blocking):

- [ ] `total_purchases_eur` excludes TRANSFER_IN carried basis at both per-holding and summary level
- [ ] `total_invested_eur` includes TRANSFER_IN carried basis (unchanged behavior)
- [ ] Individual reassignment rejects blank/missing/whitespace-only `reason` with 400
- [ ] Batch reassignment rejects blank/missing/whitespace-only `reason` with 400
- [ ] Batch reassignment is all-or-nothing: on first failure, all prior reassignments in this batch are rolled back
- [ ] Batch reassignment never returns `reassigned_count > 0` when any movement failed
- [ ] `_rollback_batch_reassign` is best-effort with per-item error logging for manual cleanup
- [ ] Preview and execution share `_fetch_reassign_candidates()` as the sole predicate source
- [ ] Preview is read-only (no Cosmos writes)
- [ ] Execution never trusts client-supplied counts or ID lists
- [ ] All four Basher xfail markers converted to passing tests
- [ ] All new tests from this document added and passing
- [ ] Zero test regressions (Phase 1 baseline + Phase 2 suite)

### MUST NOT:

- [ ] Touch files outside the scope listed in File Change Summary
- [ ] Introduce saga/operation documents (Phase 2 volumes don't warrant it)
- [ ] Add preview count validation to the execution endpoint (stale preview is acceptable)
- [ ] Change the preview response shape beyond what is specified above
- [ ] Consult Livingston for guidance (lockout applies)

---

**This document is FROZEN. Linus and Basher: execute against these specifications exactly.**
