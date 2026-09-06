# Contract: Portfolio Summary Totals & Holdings Filters

**Date:** 2026-09-06  
**Author:** Danny (Lead)  
**Status:** FROZEN — implementation-ready  
**Extends:** `danny-portfolio-implementation-contract.md` v1.1  
**Source directive:** `copilot-directive-20260906T112642+0200.md`

---

## Summary

Add three new summary totals (total purchases, total sales, current invested) to the holdings API response and UI, a default-enabled zero-shares filter, and a client-side symbol search. No new endpoints. No new backend dependencies.

---

## 1. Summary Totals — Backend

### 1.1 New fields in `HoldingsService.compute_holdings()`

Accumulate two new `Decimal` counters alongside the existing `total_invested`:

```python
total_purchases = Decimal("0")   # sum across all securities
total_sales     = Decimal("0")   # sum across all securities
```

#### Per-movement accumulation rules

| `txn_type` | Accumulator | Formula | Rationale |
|------------|-------------|---------|-----------|
| `BUY` (cost_basis_status ≠ INCOMPLETE) | `total_purchases` | `+= gross.eur_amount + fees.total_eur` | Cash outflow = principal + commission (matches existing `total_cost_eur` per-security logic) |
| `SELL` | `total_sales` | `+= gross.eur_amount - fees.total_eur` | Cash inflow = gross proceeds − commission (net cash received) |
| `DIVIDEND` | *(no change)* | Excluded from purchases/sales | Dividends tracked separately in existing `total_dividends_eur` |
| `BUY` (cost_basis_status = INCOMPLETE) | *(no change)* | Excluded (zero-cost acquisition) | Consistent with existing cost-basis exclusion |

#### Per-security fields (new)

Add to each holding dict emitted in `holdings_list`:

```python
"total_purchases_eur": str(agg["total_purchases_eur"].quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)),
"total_sales_eur":     str(agg["total_sales_eur"].quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)),
```

Where `agg["total_purchases_eur"]` mirrors the existing per-security `total_cost_eur` (same value — it IS the sum of gross+commission for completed BUYs).  
And `agg["total_sales_eur"]` = `SUM(gross.eur_amount - fees.total_eur)` for all SELLs of that security.

> **Note:** `total_purchases_eur` per-security is identical to the existing `total_invested_eur` per-security. The field is additive for clarity; the existing `total_invested_eur` per-security is preserved unchanged.

#### Summary-level fields

```python
"summary": {
    # --- existing (unchanged) ---
    "total_securities":    len(holdings_list),
    "total_invested_eur":  str(total_invested.quantize(...)),   # kept: = total_purchases (backward compat)
    "total_dividends_eur": str(total_dividends.quantize(...)),
    # --- new ---
    "total_purchases_eur": str(total_purchases.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)),
    "total_sales_eur":     str(total_sales.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)),
    "current_invested_eur": str(
        (total_purchases - total_sales).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
    ),
}
```

#### Exact semantics

| Field | Formula | Meaning |
|-------|---------|---------|
| `total_purchases_eur` | `SUM(gross.eur_amount + fees.total_eur)` for all BUY with COMPLETE cost basis | Total cash spent buying shares (principal + commissions) |
| `total_sales_eur` | `SUM(gross.eur_amount - fees.total_eur)` for all SELL | Total cash received from selling shares (gross − commissions) |
| `current_invested_eur` | `total_purchases_eur - total_sales_eur` | Net cash deployed in equities (cash-basis) |
| `total_invested_eur` | *(unchanged — same as total_purchases_eur)* | Backward-compatible alias; existing UI references preserved |
| `total_dividends_eur` | *(unchanged)* | Excluded from purchases/sales/current_invested |

**Dividends are excluded** from purchases, sales, and current_invested. They remain in `total_dividends_eur` and are displayed separately.

**`current_invested_eur` can be negative** if cumulative sell proceeds exceed cumulative purchase cost (net profitable exit). This is correct and expected.

### 1.2 `total_invested_eur` backward compatibility

The existing summary field `total_invested_eur` is **preserved unchanged** (`= total_purchases_eur`). It is NOT renamed or removed. The new `current_invested_eur` is the purchases-minus-sales field.

---

## 2. Summary Totals — Frontend Types

### 2.1 `HoldingsSummary` update (`frontend/src/types/portfolio.ts`)

```typescript
export interface HoldingsSummary {
  total_securities: number;
  total_invested_eur: string;       // kept (= total_purchases_eur)
  total_dividends_eur: string;      // kept
  total_purchases_eur: string;      // NEW
  total_sales_eur: string;          // NEW
  current_invested_eur: string;     // NEW
}
```

### 2.2 `HoldingEntry` update (`frontend/src/types/portfolio.ts`)

```typescript
export interface HoldingEntry {
  // ... all existing fields unchanged ...
  total_purchases_eur: string;      // NEW (= total_invested_eur for this security)
  total_sales_eur: string;          // NEW
}
```

---

## 3. Summary Totals — UI Display

In `PortfolioHoldingsTable.tsx`, replace the existing summary bar with:

| Position | Label | Field | Color/Style |
|----------|-------|-------|-------------|
| 1 | "Total Purchases" | `summary.total_purchases_eur` | Default text |
| 2 | "Total Sales" | `summary.total_sales_eur` | Default text |
| 3 | "Current Invested" | `summary.current_invested_eur` | Default text (accent if negative) |
| 4 | "Total Dividends" | `summary.total_dividends_eur` | `text-accent-green` (unchanged) |
| 5 | "Securities" | `summary.total_securities` | Default text |

**Summary totals are portfolio-wide and MUST NOT change when UI filters/search change.** The summary bar always reflects the full unfiltered holdings response.

The existing "Total invested" card is replaced by the new trio (Purchases / Sales / Current Invested). The "Securities" count card moves to position 5.

---

## 4. Zero-Shares Filter

### 4.1 Behavior

- **Default: enabled** (holdings with exactly zero shares are hidden from the table).
- Toggle: a checkbox/switch labeled **"Hide zero-share holdings"** in the filter toolbar.
- **Exact zero definition:** `Decimal(holding.total_shares) == Decimal("0")` in Python equivalence; in the frontend: `parseFloat(h.total_shares) === 0`.
- **Negative holdings remain visible** regardless of filter state. Negative inventory is a reconciliation warning (`NEGATIVE_INVENTORY`) and must never be hidden.
- The filter is **client-side only** — the API always returns all holdings. Filtering happens in the component render.

### 4.2 Implementation

```typescript
// In PortfolioHoldingsTable.tsx
const [hideZeroShares, setHideZeroShares] = useState(true); // default ON

const visibleHoldings = hideZeroShares
  ? holdings.filter((h) => parseFloat(h.total_shares) !== 0)
  : holdings;
```

### 4.3 Summary isolation

The summary bar reads from `data.summary` (the API response), NOT from `visibleHoldings`. Toggling the zero-shares filter changes which rows appear in the table but does **not** alter summary totals.

---

## 5. Symbol Search

### 5.1 Behavior

- Text input labeled **"Search"** in the filter toolbar, beside the zero-shares toggle.
- Searches across: `ticker`, `company_name`, `security_id` — **case-insensitive substring match**.
- **Client-side filtering.** The holdings list is already fully loaded (modest size — typically < 200 securities). No server round-trip.
- Empty search string = no filter applied (all holdings visible, subject to zero-shares filter).
- Search and zero-shares filter compose: a holding must pass both to appear.

### 5.2 Implementation

```typescript
const [searchQuery, setSearchQuery] = useState("");

const visibleHoldings = holdings.filter((h) => {
  // Zero-shares filter
  if (hideZeroShares && parseFloat(h.total_shares) === 0) return false;
  // Search filter
  if (searchQuery) {
    const q = searchQuery.toLowerCase();
    return (
      h.ticker.toLowerCase().includes(q) ||
      h.company_name.toLowerCase().includes(q) ||
      h.security_id.toLowerCase().includes(q)
    );
  }
  return true;
});
```

### 5.3 Summary isolation (reiterated)

Summary totals remain portfolio-wide. Search does not alter summary values.

---

## 6. Backend Response — Complete Field Map

### `GET /api/portfolio/holdings` response shape (after this change)

```json
{
  "holdings": [
    {
      "security_id": "XNYS:AAPL",
      "ticker": "AAPL",
      "company_name": "Apple Inc.",
      "total_shares": "70.000000",
      "avg_cost_basis_eur": "183.25",
      "cost_basis_status": "COMPLETE",
      "total_invested_eur": "18257.50",
      "total_purchases_eur": "18257.50",
      "total_sales_eur": "5492.50",
      "total_dividends_eur": "73.31",
      "accounts": ["ibkr"],
      "warnings": []
    }
  ],
  "summary": {
    "total_securities": 1,
    "total_invested_eur": "18257.50",
    "total_purchases_eur": "18257.50",
    "total_sales_eur": "5492.50",
    "current_invested_eur": "12765.00",
    "total_dividends_eur": "73.31"
  }
}
```

---

## 7. Tests

### 7.1 Backend — `backend/tests/test_portfolio_holdings.py`

Add the following test cases to the existing test file:

| Test class | Test name | Assertion |
|------------|-----------|-----------|
| `TestSummaryTotals` | `test_purchases_only` | BUY 100@€1000 (€10 fee) → `total_purchases_eur="1010.00"`, `total_sales_eur="0.00"`, `current_invested_eur="1010.00"` |
| `TestSummaryTotals` | `test_purchases_and_sales` | BUY 100@€1000 (€10 fee) + SELL 30@€350 (€5 fee) → `total_purchases_eur="1010.00"`, `total_sales_eur="345.00"`, `current_invested_eur="665.00"` |
| `TestSummaryTotals` | `test_sales_exceed_purchases` | BUY 100@€500 (€0 fee) + SELL 100@€800 (€0 fee) → `current_invested_eur="-300.00"` (profit) |
| `TestSummaryTotals` | `test_dividends_excluded_from_current_invested` | BUY + DIVIDEND → `current_invested_eur` equals `total_purchases_eur` (dividend does not reduce it) |
| `TestSummaryTotals` | `test_incomplete_buys_excluded_from_purchases` | INCOMPLETE BUY → `total_purchases_eur="0.00"` |
| `TestSummaryTotals` | `test_multi_security_aggregation` | 2 securities, mixed buys/sells → summary totals are portfolio-wide sums |
| `TestPerSecurityTotals` | `test_per_security_purchases_and_sales` | Per-holding `total_purchases_eur` and `total_sales_eur` are correct |
| `TestSummaryTotals` | `test_backward_compat_total_invested` | `summary.total_invested_eur == summary.total_purchases_eur` |

### 7.2 Frontend — No new test file required

Frontend changes are purely display/filter logic in a single component. Covered by manual verification. If the project adds component tests later, filter logic should be tested.

---

## 8. Ownership

| Change | Owner | Files |
|--------|-------|-------|
| Backend: summary accumulators + per-security sell totals | **Rusty** (Backend) | `backend/src/portfolio/holdings_service.py` |
| Backend: tests | **Rusty** (Backend) | `backend/tests/test_portfolio_holdings.py` |
| Frontend: types update | **Livingston** (Frontend) | `frontend/src/types/portfolio.ts` |
| Frontend: summary bar, filter toolbar, search | **Livingston** (Frontend) | `frontend/src/components/PortfolioHoldingsTable.tsx` |

No changes to `portfolio_routes.py` (the endpoint shape is unchanged — just new fields in the dict). No changes to `portfolio-api.ts` (the `getHoldings()` call returns `HoldingsResponse` which gains the new fields via the type update).

---

## 10. Implementation History

| Date | Agent | Action |
|------|-------|--------|
| 2026-09-06 | Rusty (Agent Dev, sub-agent) | Frontend portion implemented. Updated `HoldingsSummary` + `HoldingEntry` types with new fields (`total_purchases_eur`, `total_sales_eur`, `current_invested_eur`). Rewrote `PortfolioHoldingsTable.tsx`: summary bar now shows Total Purchases / Total Sales / Current Invested (primary, in order), then Dividends + Securities count (secondary/subtle). Added search input (ticker + company_name + security_id, case-insensitive). Added hide-zero-shares toggle (default on; negative positions always visible). Summary remains API-wide. Filtered-empty state distinct from genuinely empty portfolio with clear/reset action. Responsive + a11y attributes added. Validated: `tsc --noEmit` → 0 errors; `eslint` → 0 warnings. No backend changes, no commit/push. |

---

## 9. Non-Goals (explicitly excluded)

- No server-side filtering/search endpoint.
- No pagination of holdings (list is modest).
- No URL query params for filter/search state.
- No persistence of filter/search preferences.
- No changes to the Movements table or API.
- No new API endpoints.

---

## 10. Implementation History

| Date | Agent | Action |
|------|-------|--------|
| 2026-09-06 | Livingston | Backend: implemented `total_purchases_eur`, `total_sales_eur`, `current_invested_eur` in `HoldingsService.compute_holdings()` and per-security fields. Extended `HoldingItem` and `HoldingsSummary` Pydantic models. Added 12 targeted tests (TestSummaryTotals × 9 + TestPerSecurityTotals × 3). All 79 tests pass (33 holdings + 46 endpoints). Frontend changes deferred to separate task. |
