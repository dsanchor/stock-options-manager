# Danny — Phase 2 Revision Cycle 2 Review

**Date:** 2026-09-06  
**Author:** Danny (Lead / Final Reviewer)  
**Status:** ✅ APPROVED  
**Subject:** Linus's revision of Livingston's Phase 2 backend (4 defect fixes)  
**Predecessor:** `danny-phase2-rejection-retro.md`, `linus-phase2-revision-contract.md`

---

## Verdict: APPROVED

All four original defects are correctly resolved. The revision is safe to ship.
No lockout applies — Linus retains authorship clearance.

---

## Defect Verification

### D1 — `total_purchases_eur` is BUY-only ✅ FIXED
**File:** `backend/src/portfolio/holdings_service.py`  
Separate `total_buy_cost_eur` accumulator tracks only BUY gross+fees.  
`total_cost_eur` continues to include TRANSFER_IN carried basis for `total_invested_eur` and `avg_cost_basis_eur`.  
Summary-level `current_invested_eur = total_invested - total_sales` is semantically correct.  
Global view: transfers net to zero (TRANSFER_OUT subtracts, TRANSFER_IN adds), so no inflation.  
Per-account view: `total_purchases_eur` reflects actual purchase outflows in that account; `total_invested_eur` reflects current cost basis (post-transfer). Correct.

### D2 — Individual reassignment requires non-blank reason ✅ FIXED
**File:** `backend/web/portfolio_routes.py` ~line 842  
`reason = str(body.get("reason", "")).strip()` → `if not reason: return 400`.  
Whitespace-only, empty string, and missing key all correctly rejected.

### D3 — Batch reassignment requires non-blank reason ✅ FIXED
**File:** `backend/web/portfolio_routes.py` ~line 915  
Same pattern as D2. Missing/blank/whitespace → 400.

### D4 — Batch reassignment is all-or-nothing ✅ FIXED
**File:** `backend/src/portfolio/cosmos_portfolio.py` ~line 1020  
Fail-fast: first candidate failure triggers `_rollback_batch_reassign()`, then raises `ValueError("batch_reassign_failed: ...")`.  
Route returns 500 with `error: "batch_reassign_failed"`.  
`skipped_count` is always 0 — no silent partial results.

**Rollback correctness:**
- Deletes new docs from destination partition (correct partition key). ✅
- Restores originals to `correction_status = "ACTIVE"`, removes `superseded_by`. ✅
- Best-effort per step; failures logged as `MANUAL CLEANUP REQUIRED`. ✅
- 500 response always surfaced to caller regardless of rollback outcome. ✅

**Acknowledged trade-off:** If rollback itself partially fails, the error message says "were reversed" unconditionally. This is documented and accepted in the revision contract. The caller always receives 500 and knows the batch failed. Rollback-failure detail is logged server-side only. Adequate for Phase 2 volume.

---

## Additional Verifications (Requirements 1–10)

| # | Requirement | Status |
|---|-------------|--------|
| 1 | Accounts CRUD + delete blocking | ✅ GET/POST/GET/{id}/DELETE all correct; DELETE blocks with 409+movement_count |
| 2 | Manual BUY/SELL/DIVIDEND; SELL ACCIONES vs DERECHOS | ✅ `sales_type` defaults to ACCIONES; DERECHOS doesn't decrement shares |
| 3 | Movement detail + correction/replacement semantics | ✅ GET returns movement+superseded_by; POST /correct creates replacement, marks SUPERSEDED |
| 4 | Transfers: hard-block, preserve global shares, carry cost | ✅ InsufficientSharesError→409; global shares net zero; no purchase/sale amounts on transfers |
| 5 | `total_purchases_eur` and `current_invested_eur` semantics | ✅ See D1 above — no inflation from transfer basis |
| 6 | Reason required and trimmed | ✅ See D2/D3 above |
| 7 | Batch preview read-only, shared predicate, execution re-derives | ✅ Both use `_fetch_reassign_candidates()`; preview does no writes |
| 8 | Batch never returns partial success; rollback inspected | ✅ See D4 above |
| 9 | Frontend contracts match endpoints | ⚠️ Two integration gaps — see below |
| 10 | Existing imports, rights sales, summaries, navigation | ✅ All compatible; TopNav updated with Accounts link |

---

## Flagged Integration Gaps (Non-blocking, Outside Linus's Scope)

### Gap A: GET /api/portfolio/movements rejects TRANSFER_OUT/TRANSFER_IN filter
**File:** `backend/web/portfolio_routes.py` line 445  
**Symptom:** `if txn_type and txn_type not in {"BUY", "SELL", "DIVIDEND"}:` returns 400 for TRANSFER types.  
**Impact:** Frontend `PortfolioMovementsTable.tsx` lines 159–160 offer TRANSFER_OUT/TRANSFER_IN in the filter dropdown. Selecting them produces a 400 error.  
**Owner:** Livingston's original Phase 1 filter, not updated for Phase 2 types. Outside Linus's revision scope.  
**Fix:** Extend the allowed set: `{"BUY", "SELL", "DIVIDEND", "TRANSFER_OUT", "TRANSFER_IN"}`.

### Gap B: No PUT /api/portfolio/accounts/{account_id} endpoint
**File:** `backend/web/portfolio_routes.py` — missing route  
**Symptom:** Frontend `AccountsView.tsx` line 320 calls `updateAccount()` → PUT request → 405 Method Not Allowed.  
**Impact:** Users cannot edit existing broker accounts from the UI.  
**Owner:** Livingston's API contract never specified PUT for accounts. Rusty's frontend assumed it from the original (superseded) UI contract.  
**Fix:** Either add a PUT route or remove the edit UI. Coordinate between Livingston's successor and Rusty.

### Stale comment (cosmetic)
**File:** `frontend/src/lib/portfolio-api.ts` ~line 285  
Comment says "No preview endpoint exists in the backend" but it was added. Non-functional; just stale docs.

---

## Test Results

- **291 tests passed**, 0 failed (full Phase 2 + holdings suite).
- Four strict xfail markers successfully removed — all now passing.
- TypeScript build: exit 0, no errors.
- `conftest_portfolio_p2.py` correctly supports `delete_item` for rollback tests.

---

## Recommendation

Ship Linus's revision. Gaps A and B should be addressed in a follow-up micro-patch (assign to next available backend/frontend author — not a rejection-level issue).
