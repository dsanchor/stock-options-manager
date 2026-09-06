# Second Rejection Resolution: Portfolio Implementation — Retrospective & Correction Plan

**Date:** 2026-09-06  
**Author:** Danny (Lead / Reviewer)  
**Status:** FROZEN — authoritative second-round corrections  
**Trigger:** Two new medium bugs discovered after Linus's first-round revision  
**Contract ref:** `danny-portfolio-implementation-contract.md` v1.1  
**Predecessor:** `danny-portfolio-rejection-resolution.md` (first round — FROZEN)

---

## Rejection Lockout

Per reviewer protocol, all authors of previously rejected or revised artifacts are barred from this correction cycle:

| Agent | Role | Status |
|-------|------|--------|
| **Livingston** | Original backend author | ❌ Locked out |
| **Rusty** | Original frontend author | ❌ Locked out |
| **Linus** | First-round revision author | ❌ Locked out |
| **Danny** | Reviewer / Lead | ⛔ Does not implement (reviewer separation) |
| **Basher** | Tester / Reviewer | ⛔ Does not implement (charter restriction) |

### Correction Assignment

**All fixes in this document are assigned to a newly escalated independent general-purpose specialist** — a fresh agent with no prior involvement in the portfolio implementation. This agent receives this document as the authoritative specification.

---

## Retrospective: How These Bugs Survived First Review

### F6 — DELETE `_unassigned` movement: wrong `account_id` derivation

**Root cause:** The backend delete endpoint (`portfolio_routes.py`) contains a fallback that attempts to parse `account_id` from the movement document ID when the frontend omits it. The parsing uses `movement_id.split("_", 2)`, which breaks on `_unassigned` account IDs because `_unassigned` contains a leading underscore — the split produces an empty string at index 1, not `"_unassigned"`.

The frontend `deleteMovement()` (`portfolio-api.ts`) never sends `account_id` in the query string. The `LedgerMovement` type already carries `account_id`, and the `MovementRow` component has access to the full movement object, but the `onDelete` callback only passes `m.id`.

**Why missed in round 1:** The first-round review focused on the five identified bugs (fees, decimal parser, company_name, batch_value, dividend quantity). The delete endpoint was not in scope for those fixes, and the parsing fallback "works" for non-underscore-prefixed account IDs, so it was not exercised in test scenarios.

### F7 — `avg_cost_basis_eur` divides by transaction count, not shares

**Root cause:** `holdings_service.py` computes:
```python
cost_basis_buys = buy_count - zero_cost_count
avg_cost = total_cost / Decimal(str(cost_basis_buys))
```

This divides total cost (gross + commission) by the **number of eligible BUY transactions**, not by the **total shares purchased in those transactions**. The result is "average cost per buy transaction" — a meaningless metric — rather than "average acquisition cost per share."

**Example of the error:**
| Transaction | Shares | Gross (€) | Commission (€) | Total Cost (€) |
|-------------|--------|-----------|-----------------|-----------------|
| BUY #1 | 100 | 1,000.00 | 10.00 | 1,010.00 |
| BUY #2 | 50 | 750.00 | 5.00 | 755.00 |

- **Current (WRONG):** avg_cost = (1,010 + 755) / 2 = **€882.50** (per transaction)
- **Correct:** avg_cost = (1,010 + 755) / 150 = **€11.77** (per share)

**Why missed in round 1:** All existing tests use single-BUY scenarios (`buy_count = 1`), where dividing by 1 transaction or 1×quantity gives the same result only if you don't check the actual per-share value. The `test_complete_cost_basis` test asserts `avg_cost_basis_eur is not None` but never asserts the numeric value. The `test_commission_affects_holdings_cost_basis` test checks `total_invested_eur` (correct), not `avg_cost_basis_eur`.

---

## Finding 6: DELETE Movement — Wrong `account_id` for `_unassigned`

### Bug

**Backend (`web/portfolio_routes.py` lines ~336-339):**
```python
@router.delete("/api/portfolio/movements/{movement_id}")
async def delete_movement(
    request: Request,
    movement_id: str,
    account_id: Optional[str] = Query(default=None),
):
    if not account_id:
        # movement_id format: txn_{account_id}_{date}_{ticker}_{type}_{idx}
        parts = movement_id.split("_", 2)
        account_id = parts[1] if len(parts) > 2 else "_unassigned"
```

For movement ID `txn__unassigned_20240101_AAPL_BUY_001`:
- `split("_", 2)` → `["txn", "", "unassigned_20240101_AAPL_BUY_001"]`
- `parts[1]` = `""` (empty string)
- Cosmos `read_item(item=..., partition_key="")` → **404** (wrong partition)

**Frontend (`frontend/src/lib/portfolio-api.ts` line 100-105):**
```typescript
export async function deleteMovement(
  movementId: string,
): Promise<Pick<LedgerMovement, "id"> & { deleted_at: string }> {
  return fetchJSON(`/api/portfolio/movements/${encodeURIComponent(movementId)}`, {
    method: "DELETE",
  });
}
```
No `account_id` query parameter sent. The movement object's `account_id` is available in the component but never forwarded.

**Frontend (`frontend/src/components/PortfolioMovementsTable.tsx` line 314):**
```typescript
onClick={() => onDelete(m.id)}
```
Only `m.id` passed — `m.account_id` is discarded.

### Design Decision

**Prefer explicit `account_id` end-to-end.** Do NOT parse partition keys from document IDs — this is fragile and violates separation of concerns (the route layer should not understand ID encoding).

### Required Fix

**1. Frontend — `portfolio-api.ts`:** Add `accountId` parameter to `deleteMovement`:

```typescript
export async function deleteMovement(
  movementId: string,
  accountId?: string,
): Promise<Pick<LedgerMovement, "id"> & { deleted_at: string }> {
  const params = new URLSearchParams();
  if (accountId) params.set("account_id", accountId);
  const qs = params.toString() ? `?${params.toString()}` : "";
  return fetchJSON(`/api/portfolio/movements/${encodeURIComponent(movementId)}${qs}`, {
    method: "DELETE",
  });
}
```

**2. Frontend — `PortfolioMovementsTable.tsx`:**

a) Change `handleDelete` signature to accept both `id` and `accountId`:

```typescript
async function handleDelete(id: string, accountId?: string) {
    setDeleteError(null);
    try {
      await deleteMovement(id, accountId);
      load(offset, currentFilter);
    } catch (err) {
      const e = err as { data?: { detail?: string; error?: string } };
      setDeleteError(e.data?.detail ?? (err instanceof Error ? err.message : "Delete failed"));
    }
  }
```

b) Change `onDelete` prop type and call site in `MovementRow`:

```typescript
// Prop type:
onDelete: (id: string, accountId?: string) => void;

// Call site (line 314):
onClick={() => onDelete(m.id, m.account_id)}
```

**3. Backend — `portfolio_routes.py`:** Remove ID-parsing fallback. Default to `"_unassigned"` when `account_id` is not supplied:

```python
@router.delete("/api/portfolio/movements/{movement_id}")
async def delete_movement(
    request: Request,
    movement_id: str,
    account_id: Optional[str] = Query(default=None),
):
    """DELETE /api/portfolio/movements/{movement_id} — soft-delete a movement."""
    if not account_id:
        account_id = "_unassigned"

    try:
        svc = _get_portfolio_svc(request)
        # ... (rest unchanged)
```

### Required Tests

| Test | File | Assertion |
|------|------|-----------|
| `test_delete_unassigned_movement_no_account_id` | `test_portfolio_endpoints.py` | DELETE `txn__unassigned_20240101_AAPL_BUY_001` (no `?account_id`) → 200 (defaults to `_unassigned` partition) |
| `test_delete_movement_explicit_account_id` | `test_portfolio_endpoints.py` | DELETE `txn_broker1_20240101_AAPL_BUY_001?account_id=broker1` → 200 |
| `test_delete_movement_wrong_account_returns_404` | `test_portfolio_endpoints.py` | DELETE `txn_broker1_20240101_AAPL_BUY_001?account_id=wrong` → 404 (correct behavior: wrong partition) |
| Frontend build | `npm run build` | Build succeeds with updated signatures |

### Contract Amendment

None needed — the contract DELETE endpoint already documents `?account_id` as an optional query parameter. The implementation simply failed to make the frontend send it, and the backend fallback was wrong.

---

## Finding 7: `avg_cost_basis_eur` — Divides by Transaction Count, Not Shares

### Bug

**`holdings_service.py` lines 128-133:**
```python
cost_basis_buys = buy_count - zero_cost_count
avg_cost: Optional[Decimal] = None
if cost_basis_buys > 0 and total_cost > Decimal("0"):
    avg_cost = (total_cost / Decimal(str(cost_basis_buys))).quantize(
        _TWO_PLACES, rounding=ROUND_HALF_UP
    )
```

**`cost_basis_buys`** counts *transactions*, not *shares*. The division produces "average cost per buy event" — a meaningless number.

### Phase 1 Semantics — Authoritative Definition

**`avg_cost_basis_eur` is the average acquisition cost per share.** Precise definition:

```
avg_cost_basis_eur = total_paid_cost / total_paid_shares
```

Where:
- **`total_paid_cost`** = SUM of (gross_eur + commission_eur) for all BUY movements with `cost_basis_status != "INCOMPLETE"`
- **`total_paid_shares`** = SUM of quantity for all BUY movements with `cost_basis_status != "INCOMPLETE"`

#### Behavioral rules:

| Scenario | total_paid_cost | total_paid_shares | avg_cost_basis_eur |
|----------|-----------------|-------------------|--------------------|
| 2 BUYs: 100@€10 (€10 fee), 50@€15 (€5 fee) | 1,010 + 755 = 1,765 | 150 | 1,765 / 150 = **€11.77** |
| 1 BUY: 10@€182.50 (€7.50 fee) | 1,832.50 | 10 | **€183.25** |
| 1 BUY (INCOMPLETE, zero-cost) | 0 | 0 | **null** |
| 1 paid BUY + 1 zero-cost BUY | paid cost only | paid shares only | paid cost / paid shares |
| No BUYs (dividends only) | 0 | 0 | **null** |

#### What this metric is NOT:

- ❌ NOT remaining tax-lot basis (no FIFO/LIFO in Phase 1)
- ❌ NOT adjusted for sells (sells reduce `total_shares` but not `avg_cost_basis_eur`)
- ❌ NOT weighted by current market value

This is intentional for Phase 1: without lot-matching (FIFO/LIFO), we cannot know which shares were sold. The metric answers: "What did I pay on average to acquire my purchased shares?" — useful for quick mental comparison with current price, not for tax reporting.

#### Naming consideration:

The field name `avg_cost_basis_eur` is acceptable. "Cost basis" in a non-tax context commonly means "what I paid." Adding a clarifying tooltip in the frontend is recommended but not required for this fix. A rename to `avg_acquisition_cost_eur` was considered but rejected to avoid a breaking API change — existing consumers already read `avg_cost_basis_eur`.

### Required Fix

**`holdings_service.py`:**

1. Add `paid_buy_shares` accumulator alongside `total_cost_eur`:

```python
# In the per_security initialization dict:
"paid_buy_shares": Decimal("0"),
```

2. In the BUY branch, accumulate shares for paid buys:

```python
if txn_type == "BUY":
    agg["total_shares"] += qty
    if cost_basis_status != "INCOMPLETE":
        agg["total_cost_eur"] += gross_eur + commission_eur
        agg["paid_buy_shares"] += qty
    else:
        agg["zero_cost_count"] += 1
    agg["buy_count"] += 1
```

3. In the avg_cost computation, divide by shares not transactions:

```python
paid_shares = agg["paid_buy_shares"]
avg_cost: Optional[Decimal] = None
if paid_shares > Decimal("0") and total_cost > Decimal("0"):
    avg_cost = (total_cost / paid_shares).quantize(
        _TWO_PLACES, rounding=ROUND_HALF_UP
    )
```

4. Remove `cost_basis_buys` variable (no longer needed).

### Required Tests

| Test | File | Assertion |
|------|------|-----------|
| `test_avg_cost_basis_single_buy` | `test_portfolio_holdings.py` | 1 BUY: 10 shares, gross=€1,825, commission=€7.50 → avg_cost_basis_eur = "183.25" |
| `test_avg_cost_basis_multi_buy` | `test_portfolio_holdings.py` | BUY 100@€1,000 (€10 fee) + BUY 50@€750 (€5 fee) → avg = (1010+755)/150 = "11.77" |
| `test_avg_cost_basis_excludes_zero_cost` | `test_portfolio_holdings.py` | 1 paid BUY (100 shares, €1,000, €10 fee) + 1 INCOMPLETE BUY (50 shares, €0) → avg = 1010/100 = "10.10" |
| `test_avg_cost_basis_no_paid_buys_is_null` | `test_portfolio_holdings.py` | Only INCOMPLETE BUYs → avg_cost_basis_eur is None |
| `test_avg_cost_basis_independent_of_sells` | `test_portfolio_holdings.py` | BUY 100@€1,000 (€10 fee) then SELL 30 → avg = 1010/100 = "10.10" (unchanged by sell) |
| `test_avg_cost_basis_dividends_only_is_null` | `test_portfolio_holdings.py` | Only DIVIDEND movements → avg_cost_basis_eur is None |

### Contract Amendment

Append to contract §Holdings response, `avg_cost_basis_eur` field documentation:

> **`avg_cost_basis_eur`** (string | null): Average acquisition cost per share in EUR. Computed as `SUM(gross_eur + commission_eur) / SUM(quantity)` across paid BUY movements (excluding zero-cost/INCOMPLETE acquisitions). Independent of sells — not adjusted for lot disposition (Phase 1 has no FIFO/LIFO). Returns `null` when no paid BUY movements exist.

---

## Correction Sequence

Both fixes are independent:

```
F6 — DELETE account_id  (frontend + backend, small blast radius)
F7 — avg_cost_basis_eur (backend only, arithmetic fix + tests)
```

Can be implemented in parallel or in either order.

### File Change Summary

| File | Finding | Change |
|------|---------|--------|
| `backend/web/portfolio_routes.py` | F6 | Remove ID-parsing fallback; default `account_id` to `"_unassigned"` |
| `backend/src/portfolio/holdings_service.py` | F7 | Add `paid_buy_shares` accumulator; divide by shares not transactions |
| `frontend/src/lib/portfolio-api.ts` | F6 | Add `accountId` param to `deleteMovement()` |
| `frontend/src/components/PortfolioMovementsTable.tsx` | F6 | Pass `m.account_id` through `onDelete` callback |
| `backend/tests/test_portfolio_endpoints.py` | F6 | Add 3 DELETE tests (unassigned default, explicit account, wrong account) |
| `backend/tests/test_portfolio_holdings.py` | F7 | Add 6 avg_cost_basis_eur tests (single, multi, zero-cost, null, sells, dividends) |

### Validation Gate

After all corrections:

1. `cd backend && python -m pytest tests/ -x` — all tests pass (existing + new)
2. `cd frontend && npm run build` — build succeeds
3. Basher runs full test suite validation
4. Danny re-reviews the corrected diff

---

## Summary of Contract Amendments

| § | Amendment |
|---|-----------|
| Holdings response, `avg_cost_basis_eur` | Document precise semantics: paid BUY (gross+commission) / paid BUY shares, excluding zero-cost, independent of sells, null when no paid BUYs |

All other contract shapes, enums, and endpoint paths remain unchanged.

---

**This document is FROZEN. The assigned specialist and Basher: execute against these specifications exactly.**
