# Second Rejection Implementation Notes

**Date:** 2026-09-06  
**Author:** Reuben (escalated independent specialist)  
**Status:** COMPLETE — all tests pass  
**Ref:** `danny-portfolio-second-rejection-resolution.md`

---

## F6 — DELETE `account_id` Partition Key

### Files Changed

| File | Change |
|------|--------|
| `backend/web/portfolio_routes.py` | Removed ID-parsing fallback; `if not account_id: account_id = "_unassigned"` |
| `frontend/src/lib/portfolio-api.ts` | Added `accountId?: string` param; builds `?account_id=` query string via URLSearchParams |
| `frontend/src/components/PortfolioMovementsTable.tsx` | `handleDelete(id, accountId?)`, `onDelete: (id, accountId?) => void`, call site passes `m.account_id` |
| `backend/tests/test_portfolio_endpoints.py` | Added partition-key check to `FakePortfolioContainer.read_item`; added `TestF6DeleteMovementAccountId` with 3 tests |

### Test Results

- `test_delete_unassigned_movement_no_account_id` — PASSED (omitted `?account_id` defaults to `_unassigned`)
- `test_delete_movement_explicit_account_id` — PASSED (explicit `?account_id=broker1`)
- `test_delete_movement_wrong_account_returns_404` — PASSED (wrong partition → 404)

---

## F7 — `avg_cost_basis_eur` Per-Share Fix

### Files Changed

| File | Change |
|------|--------|
| `backend/src/portfolio/holdings_service.py` | Added `paid_buy_shares` accumulator to per_security init; increments with qty on paid BUYs; divides `total_cost / paid_shares` instead of `total_cost / cost_basis_buys`; removed `cost_basis_buys` variable |
| `backend/tests/test_portfolio_holdings.py` | Added `TestAvgCostBasisPerShare` with 6 tests |

### Test Results

- `test_avg_cost_basis_single_buy` — PASSED (1832.50/10 = 183.25)
- `test_avg_cost_basis_multi_buy` — PASSED (1765/150 = 11.77)
- `test_avg_cost_basis_excludes_zero_cost` — PASSED (1010/100 = 10.10)
- `test_avg_cost_basis_no_paid_buys_is_null` — PASSED (null)
- `test_avg_cost_basis_independent_of_sells` — PASSED (sell does not alter avg)
- `test_avg_cost_basis_dividends_only_is_null` — PASSED (null)

---

## Validation

```
cd backend && python3 -m pytest tests/test_portfolio_holdings.py tests/test_portfolio_endpoints.py -x -v
# 53 passed, 3 warnings in 4.75s

cd frontend && npx tsc --noEmit
# exit 0 — no type errors
```
