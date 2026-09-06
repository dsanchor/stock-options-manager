# Rejection Resolution: Portfolio Implementation — Retrospective & Correction Plan

**Date:** 2026-09-06  
**Author:** Danny (Lead / Reviewer)  
**Status:** FROZEN — authoritative amendments to contract v1.1  
**Trigger:** REJECT of Livingston's backend implementation and Rusty's frontend implementation  
**Contract ref:** `danny-portfolio-implementation-contract.md` v1.1

---

## Rejection Lockout

Per reviewer protocol, the original authors of the rejected artifacts are barred from revising them:

| Agent | Rejected Artifact | Status |
|-------|-------------------|--------|
| **Livingston** | Backend portfolio implementation | ❌ Locked out — may not revise |
| **Rusty** | Frontend portfolio implementation | ❌ Locked out — may not revise |

### Correction Assignments

| Finding | Backend Fix Owner | Frontend Fix Owner | Rationale |
|---------|-------------------|--------------------|-----------|
| F1 — Fees/commission | **Linus** | N/A | Quant Dev; numeric correctness is squarely in charter |
| F2 — Spanish decimals | **Linus** | N/A | Parser/numeric fix, backend only |
| F3 — Preview company_name | **Linus** | N/A | Backend response shape fix only; frontend type + render already correct |
| F4 — BATCH_VALUE field name | **Linus** | **Linus** | Frontend fix is a 1-line field rename (`value` → `batch_value`); Basher cannot implement feature code by charter; Linus is the only eligible agent. Safe: change is mechanical, no UX/design judgment required. |
| F5 — Dividend quantity model | **Linus** | **Linus** | Backend model + response shape amendment; frontend type update needed to match |

**Danny** does not implement production fixes (reviewer separation).  
**Basher** validates all corrections via test execution (existing + new tests).

---

## Finding 1: Fees/Commission Dropped on Commit

### Bug

`import_service._row_to_movement()` hardcodes fees to `"0.00"`:

```python
# import_service.py line ~627
"fees": {
    "total": "0.00",        # ← BUG: always zero
    "currency": currency,
    "total_eur": "0.00",    # ← BUG: always zero
},
```

The `commission` field is correctly parsed by `purchases.py` and `sales.py` and is used to compute `net = gross - commission`, but the commission value itself is never stored in the `fees` field of the committed `ledger_txn`. Consequently:

- `holdings_service.py` reads `fees.total_eur` and adds it to `total_cost_eur` for BUY cost basis — but since fees is always `"0.00"`, commissions are silently lost from cost basis.
- The preview `fees_eur` column always shows `"0.00"`.

### Required Fix

In `_row_to_movement()`, populate `fees` from the parsed row:

```python
# For purchases and sales:
commission = row.get("commission", Decimal("0"))

"fees": {
    "total": str(commission.normalize()),
    "currency": currency,
    "total_eur": str(commission.normalize()),
},
```

For dividends, fees remain `"0.00"` (the dividend CSV schema has no commission column).

### Required Tests

| Test | File | Assertion |
|------|------|-----------|
| `test_purchase_commission_in_fees` | `test_portfolio_import_service.py` | A purchase with `Comisión = 7,50` → committed movement has `fees.total_eur = "7.50"` |
| `test_sale_commission_in_fees` | `test_portfolio_import_service.py` | A sale with `Comisión = 5,00` → committed movement has `fees.total_eur = "5.00"` |
| `test_dividend_fees_zero` | `test_portfolio_import_service.py` | Dividend movement always has `fees.total_eur = "0.00"` |
| `test_commission_affects_holdings_cost_basis` | `test_portfolio_holdings.py` | Purchase `Total (€) = 1000`, `Comisión = 10` → holding `total_invested_eur = "1010.00"` |
| `test_preview_shows_fees` | `test_portfolio_endpoints.py` | Preview response movement has `fees_eur = "7.50"` for a purchase with 7.50 commission |

### Contract Amendment

No contract text change needed. The contract already specifies `fees.total` and `fees.total_eur` in the movement response shape. The implementation simply failed to populate them.

---

## Finding 2: Spanish Decimal Parser — Dot-Only String Ambiguity

### Bug

`parse_spanish_decimal()` only applies the Spanish convention (dots = thousands, comma = decimal) when the string contains a comma:

```python
if "," in s:
    s = s.replace(".", "").replace(",", ".")
# Falls through to Decimal(s) for dot-only strings
```

For the Spanish historical schemas this parser serves, a string like `"1.234"` (no comma) is ambiguous:
- English interpretation: `1.234` (one point two three four)
- Spanish interpretation: `1234` (one thousand two hundred thirty four)

The user's pasted data always uses Spanish conventions (decimal comma). A dot-only string in this context means the dot is a thousands separator with no decimal part. Examples from real data: `"1.000"` means 1000, `"10.500"` means 10500.

### Authoritative Rule

**In this parser (Spanish historical schemas), ALL dots are thousands separators.** A dot-only string (no comma present) must strip dots:

| Input | Current (wrong) | Correct |
|-------|-----------------|---------|
| `"1.234,56"` | `1234.56` ✅ | `1234.56` |
| `"1.234"` | `1.234` ❌ | `1234` |
| `"10.500"` | `10.500` ❌ | `10500` |
| `"1.000"` | `1.000` ❌ | `1000` |
| `"100"` | `100` ✅ | `100` |
| `"0"` | `0` ✅ | `0` |
| `"0,50"` | `0.50` ✅ | `0.50` |

### Required Fix

In `parse_spanish_decimal()`, always strip dots as thousands separators:

```python
def parse_spanish_decimal(raw: str) -> Optional[Decimal]:
    # ... null/empty checks ...
    s = str(raw).strip()
    # ... N/A checks ...

    # Spanish convention: dots are ALWAYS thousands separators
    s = s.replace(".", "")
    # Comma (if present) is the decimal separator
    s = s.replace(",", ".")

    try:
        return Decimal(s)
    except InvalidOperation:
        raise ValueError(f"Cannot parse decimal: {raw!r}")
```

### Required Tests

| Test | File | Assertion |
|------|------|-----------|
| `test_dot_only_is_thousands` | `test_portfolio_parsers.py` | `parse_spanish_decimal("1.234")` → `Decimal("1234")` |
| `test_dot_only_large` | `test_portfolio_parsers.py` | `parse_spanish_decimal("10.500")` → `Decimal("10500")` |
| `test_dot_only_round_thousand` | `test_portfolio_parsers.py` | `parse_spanish_decimal("1.000")` → `Decimal("1000")` |
| `test_no_dot_no_comma` | `test_portfolio_parsers.py` | `parse_spanish_decimal("100")` → `Decimal("100")` (unchanged) |
| `test_comma_decimal_preserved` | `test_portfolio_parsers.py` | `parse_spanish_decimal("1.234,56")` → `Decimal("1234.56")` (unchanged) |
| `test_comma_only_decimal` | `test_portfolio_parsers.py` | `parse_spanish_decimal("0,50")` → `Decimal("0.50")` (unchanged) |

### Contract Amendment

Append to contract §CSV Schemas preamble:

> **Decimal parsing rule (authoritative):** In all three domain parsers, dots are ALWAYS thousands separators and commas are ALWAYS decimal separators. A dot-only string (no comma) has its dots stripped; it does NOT fall through to English decimal interpretation. This parser is exclusively for Spanish historical schemas.

---

## Finding 3: Preview Response Missing `company_name`

### Bug

`_build_preview_response()` builds preview movement dicts without `company_name`:

```python
preview_movements.append({
    "row_index": row_idx,
    "txn_type": m.get("txn_type"),
    "security_id": m.get("security_id"),
    "ticker": m.get("ticker"),
    "trade_date": m.get("trade_date"),
    # ... no company_name ...
})
```

The contract preview response (§POST /api/import/sessions/{session_id}/preview) specifies:
```json
{ "company_name": "Apple Inc.", ... }
```

The frontend `PreviewMovement` type declares `company_name: string` and `ImportPreview.tsx` line 124 renders `{m.company_name}`, which will be `undefined`.

### Source/Precedence Rule

The `company_name` in preview movements must come from the **resolved security_master** document (via `resolution_map` → `security_id` → `securities_svc.get_security()`), NOT from the raw CSV `empresa_raw`. Rationale: the user just resolved the entity question; the preview should reflect the canonical security name, not the original CSV string.

If the security_master lookup fails (defensive), fall back to the parsed row's `empresa_raw`.

### Required Fix

In `_build_preview_response()`, resolve company names and include them:

```python
# Resolve company names from security_master
security_names = {}
for m in movements:
    sid = m.get("security_id", "")
    if sid and sid not in security_names:
        # Best effort: try security catalog, fall back to empresa_raw from source
        security_names[sid] = ""  # populated below

# ... (batch lookup or per-id lookup from securities_svc)

preview_movements.append({
    ...
    "company_name": security_names.get(m.get("security_id", ""), ""),
    ...
})
```

The same pattern already exists in `holdings_service._resolve_security_names()` and can be reused.

### Required Tests

| Test | File | Assertion |
|------|------|-----------|
| `test_preview_includes_company_name` | `test_portfolio_endpoints.py` | Preview movement has non-empty `company_name` field |
| `test_preview_company_name_from_security_master` | `test_portfolio_import_service.py` | With a resolved security named "Apple Inc.", preview movement `company_name == "Apple Inc."` |

### Contract Amendment

None needed — the contract already specifies `company_name` in the preview shape. This is a conformance fix.

---

## Finding 4: `batch_value` Field Name Mismatch (Frontend → Backend)

### Bug

The contract specifies the answer request body uses `batch_value`:
```json
{ "question_id": "...", "answer_type": "BATCH_VALUE", "batch_value": "USD" }
```

The backend reads `batch_value`:
```python
# import_service.py line 188
batch_value = answer_request.get("batch_value")
```

But the frontend sends `value` instead:
```typescript
// ImportQuestionCard.tsx line 62
await submit({
    question_id: question.question_id,
    answer_type: "BATCH_VALUE",
    value: batchValue,        // ← WRONG: should be batch_value
});
```

And the frontend type declares `value`:
```typescript
// import.ts, ImportAnswer interface
value?: string;               // ← WRONG: should be batch_value
```

This means all BATCH_VALUE answers silently fail — the backend receives `batch_value = None` and falls back to defaults (`"EUR"` for currency, `"_unassigned"` for account_id).

### Required Fix

**Frontend (Linus):**

1. `frontend/src/types/import.ts` — `ImportAnswer` interface: rename `value` → `batch_value`

```typescript
export interface ImportAnswer {
  question_id: string;
  answer_type: AnswerType;
  selected_security_id?: string;
  batch_value?: string;           // ← was: value
}
```

2. `frontend/src/components/ImportQuestionCard.tsx` line 62:

```typescript
await submit({
    question_id: question.question_id,
    answer_type: "BATCH_VALUE",
    batch_value: batchValue,       // ← was: value
});
```

3. `frontend/src/components/ImportQuestionCard.tsx` line 223 (`answerSummary`):

```typescript
// Update reference from a.value to a.batch_value
return `...: ${a.batch_value}`;
```

### Required Tests

| Test | File | Assertion |
|------|------|-----------|
| `test_batch_value_field_reaches_backend` | `test_portfolio_endpoints.py` | POST answer with `{"answer_type": "BATCH_VALUE", "batch_value": "USD"}` → session currency updated to `"USD"` |
| Frontend build | `npm run build` | Build succeeds with renamed field |

### Contract Amendment

None needed — the contract already specifies `batch_value`. The frontend implementation deviated.

---

## Finding 5: Dividend Quantity Model — Semantic Resolution

### Ambiguity

The dividend CSV schema has **no quantity/shares column**. The current implementation fabricates `quantity = Decimal("0")` for dividend movements:

```python
# import_service.py _row_to_movement(), dividends branch:
quantity = Decimal("0")
```

This is semantically wrong. `quantity = 0` implies "zero shares" — a meaningful numeric assertion. But a cash dividend event simply has **no quantity**; the concept doesn't apply. The dividend is characterized by its gross/net amounts, not by a share count.

The contract preview example shows `"quantity": "100"` for a DIVIDEND — but this is aspirational for a future phase where dividend-per-share calculations exist. Phase 1 has no share count data for dividends.

Meanwhile, holdings_service correctly doesn't use dividend quantity for share calculations (it only tracks BUY/SELL). So the quantity field on dividends is purely presentational.

### Authoritative Decision

**Dividend movements: `quantity` is `null` (omitted/nullable).** This is the semantically correct model for Phase 1:

- **Nullable quantity** for `DIVIDEND` rows — the CSV has no share count, and fabricating `0` or `1` would be misleading.
- **Required quantity** for `BUY` and `SELL` rows — these always have an `Acciones` column.
- Holdings computation: unchanged. `holdings_service` already ignores dividend quantities.
- The `quantity` field on `ledger_txn` and preview movements becomes `Optional[str]` (nullable string) instead of required string.

This cleanly distinguishes:
- **Holdings-impacting quantities** (BUY/SELL): always present, always numeric, affect `total_shares`.
- **Cash events** (DIVIDEND): no quantity; characterized by monetary amounts only.

### Required Fix

**Backend (Linus):**

1. `import_service._row_to_movement()` — dividends branch:

```python
if fmt == "dividends":
    quantity = None     # ← was: Decimal("0")
```

2. Movement serialization: `"quantity"` field must accept `None` and serialize as `null` in JSON.

3. `_build_preview_response()`: preview movement `"quantity"` may be `null`.

4. `holdings_service.py`: no change needed — already handles missing/zero quantity gracefully.

**Frontend (Linus):**

1. `frontend/src/types/import.ts` — `PreviewMovement`:

```typescript
quantity: string | null;    // ← was: string
```

2. `frontend/src/types/portfolio.ts` — `LedgerMovement` (if quantity field exists):

```typescript
quantity?: string | null;   // nullable for DIVIDEND
```

3. `frontend/src/components/ImportPreview.tsx` line rendering quantity:

```typescript
<td>{m.quantity ?? "—"}</td>   // Display em dash for null
```

4. `frontend/src/components/PortfolioMovementsTable.tsx` — same null guard on quantity display.

### Required Tests

| Test | File | Assertion |
|------|------|-----------|
| `test_dividend_quantity_null` | `test_portfolio_import_service.py` | Dividend movement has `quantity is None` (not `"0"`) |
| `test_buy_quantity_present` | `test_portfolio_import_service.py` | BUY movement has non-null `quantity` string |
| `test_sell_quantity_present` | `test_portfolio_import_service.py` | SELL movement has non-null `quantity` string |
| `test_preview_dividend_quantity_null` | `test_portfolio_endpoints.py` | Preview DIVIDEND movement returns `"quantity": null` in JSON |
| `test_holdings_ignores_dividend_quantity` | `test_portfolio_holdings.py` | Holdings `total_shares` unaffected by dividend (with null quantity) |
| Frontend build | `npm run build` | Build succeeds with nullable quantity type |

### Contract Amendment — Response Shape Update

Amend the preview movement shape (§POST .../preview Response 200):

```json
{
  "quantity": "100"          // string | null — null for DIVIDEND (no share count in source)
}
```

Amend the movements response shape (§GET /api/portfolio/movements Response 200):

```json
{
  "quantity": "100.000000"   // string | null — null for DIVIDEND
}
```

Add to contract §CSV Schemas preamble:

> **Quantity invariant:** `quantity` is required (non-null string) for BUY and SELL movements. `quantity` is `null` for DIVIDEND movements — the dividend CSV schema has no share count column; the system must not fabricate a quantity.

---

## Correction Sequence

All corrections are independent and can be implemented in parallel. Recommended order for Linus:

```
1. F2 — Spanish decimals          (parser fix, smallest blast radius)
2. F1 — Fees/commission            (import_service fix)
3. F3 — Preview company_name       (import_service + needs securities_svc access in preview)
4. F5 — Dividend quantity null      (model change, backend + frontend)
5. F4 — BATCH_VALUE field name      (frontend-only, mechanical)
```

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
| CSV Schemas preamble | Add decimal parsing rule: dots are ALWAYS thousands separators |
| CSV Schemas preamble | Add quantity invariant: null for DIVIDEND, required for BUY/SELL |
| Preview response shape | `quantity` becomes `string \| null` |
| Movements response shape | `quantity` becomes `string \| null` |

All other contract shapes, enums, and endpoint paths remain unchanged. These amendments are additive clarifications, not breaking changes.

---

**This document is FROZEN. Linus and Basher: execute against these specifications exactly.**
