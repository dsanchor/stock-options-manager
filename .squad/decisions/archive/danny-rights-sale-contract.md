# Rights Sales Column Design: Portfolio Sales CSV Extension

**Date:** 2026-09-06  
**Author:** Danny (Lead / Architecture)  
**Status:** DESIGN REVIEW — awaiting implementation (Livingston) and testing (Basher)  
**Scope:** Sales CSV 7th column "Tipo" (Acciones | Derechos)  
**Trigger:** User requirement: sales-of-rights must NOT decrement holdings, only record proceeds  
**Context:** Existing 6-column sales CSV backward-compatible  

---

## Executive Summary

**The Problem:**
- Sales CSVs currently have 6 columns: Año, Empresa, Fecha venta, Acciones, Comisión, Total Venta
- Users need to distinguish between sales of shares ("Acciones") and sales of rights ("Derechos")
- Key requirement: **Acciones sales decrement holdings; Derechos sales do NOT**
- Existing 6-column CSVs have no Tipo column and must remain valid (backward-compatible)

**The Solution:**
1. Add optional 7th column "Tipo" (normalized to "ACCIONES" | "DERECHOS")
2. Default 6-column CSVs to "ACCIONES" (transparent to user, existing behavior preserved)
3. Extend `LedgerMovement` domain model with `sales_type` field (read-only, derived from Tipo)
4. Modify holdings computation: DERECHOS sales do NOT decrement quantity
5. Add normalization and validation rules; emit warnings for edge cases
6. Extend API/frontend to surface sales type in preview and holdings

**No TxnType change:** Both remain SELL; sales_type differentiates behavior.

---

## Detailed Design

### 1. Domain Model Extension

#### 1.1 Parser Output (sales.py)

**Current output** (6 columns):
```python
{
    "row_index": 0,
    "year": 2024,
    "empresa_raw": "Apple Inc",
    "empresa_normalized": "apple inc",
    "sale_date": "2024-03-15",
    "quantity": Decimal("100"),
    "commission": Decimal("50"),
    "total_proceeds": Decimal("18950"),
    "source_row": {...},
    "warnings": []
}
```

**New output** (6 or 7 columns):
```python
{
    "row_index": 0,
    "year": 2024,
    "empresa_raw": "Apple Inc",
    "empresa_normalized": "apple inc",
    "sale_date": "2024-03-15",
    "quantity": Decimal("100"),           # Now optional for DERECHOS
    "commission": Decimal("50"),
    "total_proceeds": Decimal("18950"),
    "sales_type": "ACCIONES",             # NEW: normalized value
    "sales_type_raw": "Acciones",         # NEW: source value
    "source_row": {...},
    "warnings": [
        # Warnings for edge cases (see §Edge Cases)
    ]
}
```

#### 1.2 LedgerMovement (models.py / portfolio.ts)

Add two new **optional** fields to `LedgerMovement`:

```python
# backend/src/portfolio/models.py
class LedgerMovement(BaseModel):
    # ... existing fields ...
    txn_type: TxnType        # "SELL" for both Acciones and Derechos
    
    # NEW — set only for SELL transactions
    sales_type: Optional[str] = None    # "ACCIONES" or "DERECHOS"; None for BUY/DIVIDEND
    is_rights_sale: Optional[bool] = None  # True if sales_type=="DERECHOS"; convenience flag
```

```typescript
// frontend/src/types/portfolio.ts
export interface LedgerMovement {
    // ... existing fields ...
    txn_type: TxnType;
    
    // NEW
    sales_type?: "ACCIONES" | "DERECHOS" | null;     // For SELL only
    is_rights_sale?: boolean | null;  // Convenience: is_rights_sale === (sales_type === "DERECHOS")
}
```

#### 1.3 Holdings Computation

**Current rule** (preserve unchanged):
```
total_shares = SUM(BUY.quantity) - SUM(SELL.quantity)
```

**New rule**:
```
total_shares = SUM(BUY.quantity) - SUM(SELL[sales_type=="ACCIONES"].quantity)
# Note: SELL[sales_type=="DERECHOS"] does NOT contribute to share count
# Both ACCIONES and DERECHOS contribute to total_sales_eur
```

---

### 2. CSV Parsing: Normalization & Validation

#### 2.1 Column Detection

**6-column CSV** (existing):
```
Año | Empresa | Fecha venta | Acciones | Comisión | Total Venta
```
→ Auto-detected; Tipo defaults to "ACCIONES" (transparent)

**7-column CSV** (new):
```
Año | Empresa | Fecha venta | Acciones | Comisión | Total Venta | Tipo
```
→ Auto-detected; Tipo is read and normalized

**Column validation:**
- Positions 1–6 must exist and match expected headers (via normalized comparison)
- Position 7 (Tipo) is optional
- If position 7 exists but is empty/whitespace: treat as "ACCIONES" (fail-safe)
- If position 7 is present and non-empty: must normalize to valid value; otherwise → parse error

#### 2.2 Tipo Normalization Rules

**Input → Normalized Output:**

| Input | Normalized | Valid? |
|-------|-----------|--------|
| "Acciones" | "ACCIONES" | ✓ |
| "acciones" | "ACCIONES" | ✓ |
| "ACCIONES" | "ACCIONES" | ✓ |
| "Acciónes" (typo) | N/A | ✗ Parse error |
| "Derechos" | "DERECHOS" | ✓ |
| "derechos" | "DERECHOS" | ✓ |
| "DERECHOS" | "DERECHOS" | ✓ |
| "" (empty) | "ACCIONES" | ✓ (fallback) |
| "Otro" | N/A | ✗ Parse error |

**Normalization algorithm:**
1. Strip leading/trailing whitespace
2. Apply Unicode NFKD decomposition and remove combining characters (accent-insensitive)
3. Convert to uppercase
4. Match against `{"ACCIONES", "DERECHOS"}`
5. If no match and not empty: raise `ValueError`
6. If empty: default to "ACCIONES"

**Code location:** `backend/src/portfolio/parsers/sales.py` → new function `_normalize_sales_type(raw_value: str) -> str`

---

### 3. Persistence & Ledger Schema

#### 3.1 Movement Document (Cosmos)

Add fields to `ledger_txn` document:

```json
{
  "id": "txn__unassigned_20240315_AAPL_SELL_001",
  "txn_type": "SELL",
  "sales_type": "ACCIONES",           // NEW
  "sales_type_raw": "Acciones",       // NEW: for audit trail
  "quantity": "100",
  "gross": { "eur_amount": "18950" },
  "fees": { "total_eur": "50" },
  "net": { "eur_amount": "18900" },
  // ... rest unchanged ...
}
```

#### 3.2 Backward Compatibility

- Existing `ledger_txn` documents without `sales_type` field are treated as "ACCIONES" (existing behavior)
- No data migration needed; defaulting at read time is safe
- API and frontend handle `sales_type: null` as "ACCIONES"

---

### 4. Holdings Computation Rules

#### 4.1 Share Quantity Calculation

**In `holdings_service.py`:**

```python
for m in movements:
    txn_type = m.get("txn_type", "")
    
    if txn_type == "BUY":
        agg["total_shares"] += qty
    elif txn_type == "SELL":
        sales_type = m.get("sales_type", "ACCIONES")  # Default to ACCIONES
        if sales_type == "ACCIONES":
            agg["total_shares"] -= qty
        # else: DERECHOS sale — do NOT decrement
    elif txn_type == "DIVIDEND":
        # ... unchanged ...
```

#### 4.2 Sales Amount Tracking (unchanged)

Both ACCIONES and DERECHOS sales contribute to `total_sales_eur`:

```python
if txn_type == "SELL":
    agg["total_sales_eur"] += gross_eur - commission_eur
    # sales_type doesn't affect this — both types record proceeds
```

#### 4.3 Example Holdings Calculation

**Movements:**
| Type | Sales Type | Qty | Gross | Commission |
|------|-----------|-----|-------|-----------|
| BUY | — | 100 | €2,000 | €20 |
| SELL | ACCIONES | 30 | €600 | €5 |
| SELL | DERECHOS | 15 | €300 | €5 |

**Holdings:**
- `total_shares` = 100 - 30 = **70** (DERECHOS sale NOT subtracted)
- `total_invested_eur` = (2,000 + 20) = €2,020
- `total_sales_eur` = (600 - 5) + (300 - 5) = **€890** (both types counted)
- `avg_cost_basis_eur` = 2,020 / 100 = **€20.20**

---

### 5. Summary Behavior

#### 5.1 Warnings During Import

**New warning type:** (extend existing `WarningType` enum)

```python
class WarningType(str, Enum):
    NEGATIVE_INVENTORY = "NEGATIVE_INVENTORY"
    ZERO_COST_ACQUISITION = "ZERO_COST_ACQUISITION"
    RIGHTS_AMOUNT = "RIGHTS_AMOUNT"
    PROBABLE_DUPLICATE = "PROBABLE_DUPLICATE"
    DERECHOS_WITH_QUANTITY = "DERECHOS_WITH_QUANTITY"      # NEW
    ACCIONES_ZERO_QUANTITY = "ACCIONES_ZERO_QUANTITY"      # NEW
    INVALID_SALES_TYPE = "INVALID_SALES_TYPE"              # NEW
```

#### 5.2 Warning Rules

| Condition | Warning Type | Severity | Blocking? | Message |
|-----------|--------------|----------|-----------|---------|
| `sales_type == "DERECHOS"` AND `quantity > 0` | `DERECHOS_WITH_QUANTITY` | Medium | No | Rights sales should not have a share quantity. Please verify. |
| `sales_type == "ACCIONES"` AND `quantity == 0` | `ACCIONES_ZERO_QUANTITY` | Medium | No | Share sale with zero quantity. Verify row is not a rights transaction. |
| `sales_type == "DERECHOS"` AND `total_proceeds <= 0` | `DERECHOS_AMOUNT` | Medium | No | Rights sale with zero or negative proceeds; verify row is complete. |
| Tipo column present but value fails normalization | `INVALID_SALES_TYPE` | High | **Yes** | `Row {row_index}: Invalid Tipo value '{raw}'; must be 'Acciones' or 'Derechos'.` |

#### 5.3 Preview Display

**PreviewMovement** (frontend type):
```typescript
export interface PreviewMovement {
    // ... existing fields ...
    txn_type: string;
    sales_type?: "ACCIONES" | "DERECHOS";  // NEW: show type for SELL rows
    warnings?: MovementWarning[];
}
```

**Preview UI behavior:**
- For SELL rows with `sales_type == "DERECHOS"`: display badge "Derechos (no share impact)"
- For SELL rows with `sales_type == "ACCIONES"` or missing: display as normal (or badge optional)
- In warnings section: show any `DERECHOS_WITH_QUANTITY`, `ACCIONES_ZERO_QUANTITY` warnings

---

### 6. API Contract Changes

#### 6.1 No Breaking Changes

All changes are **additive** and backward-compatible:
- `sales_type` and `is_rights_sale` are optional fields in responses
- Existing clients ignore new fields
- Defaults ensure old behavior for existing data

#### 6.2 Export/Download

If a user exports/downloads holdings or movement ledger:
- Include `sales_type` column in CSV for SELL rows
- For existing data without `sales_type`: export as "ACCIONES" (implicit default)

---

### 7. Edge Cases & Error Handling

#### 7.1 Parsing Errors

| Scenario | Behavior |
|----------|----------|
| 7-column CSV, Tipo is invalid (e.g., "Accione") | **Parse error**: `Row 2: Invalid Tipo value 'Accione'; must be 'Acciones' or 'Derechos'.` |
| 7-column CSV, Tipo column is empty/null | Treat as "ACCIONES" (safe default, no warning) |
| 6-column CSV (Tipo missing) | Treat as "ACCIONES" (backward-compatible) |
| Quantity is 0, sales_type is "ACCIONES" | **Warning** `ACCIONES_ZERO_QUANTITY`, not error |
| Quantity > 0, sales_type is "DERECHOS" | **Warning** `DERECHOS_WITH_QUANTITY`, not error |
| Proceeds are negative, sales_type is "DERECHOS" | **Warning** `DERECHOS_AMOUNT`, not error |

#### 7.2 Holdings Edge Cases

| Scenario | Behavior |
|----------|----------|
| User imports: 100 AAPL BUY, then 80 AAPL SELL (DERECHOS) | Holdings: 100 shares. Warning on DERECHOS transaction: "Rights sale does not affect share count." |
| User imports: 100 AAPL SELL (ACCIONES), no buys | Holdings: -100 shares. Warning: `NEGATIVE_INVENTORY` (existing). |
| User imports: 50 AAPL SELL (DERECHOS), 60 AAPL SELL (ACCIONES), no buys | Holdings: -60 shares. `total_sales_eur` includes both. |

#### 7.3 Duplicate Detection

- Duplicate detection (via `row_idempotency_hash`) uses: `security_id`, `txn_type`, `trade_date`, `quantity`, `gross`
- `sales_type` is **NOT** included in the hash
- Rationale: Two sales of the same security on the same date with same quantity and amount are duplicates regardless of type
- **Caveat:** If user imports "100 AAPL sold 2024-03-15, Acciones" then "100 AAPL sold 2024-03-15, Derechos", second will be flagged as duplicate. This is correct (real data issue).

---

## Implementation Guidance

### For Livingston (Backend Implementation)

#### Phase 1: Parser Extension

1. **File:** `backend/src/portfolio/parsers/sales.py`
   - Modify `_REQUIRED_COLS` validation to allow optional 7th column (Tipo)
   - Add `_normalize_sales_type(raw: str) -> str` function with rules from §2.2
   - Update `parse_sales()` to:
     - Read position 7 if present
     - Normalize and validate
     - Default to "ACCIONES" if missing/empty
     - Return `sales_type` and `sales_type_raw` in each row dict
     - Emit warnings for edge cases per §5.2

2. **Test coverage:**
   - 6-column CSV → all rows default to "ACCIONES"
   - 7-column CSV with "Acciones" → normalized to "ACCIONES"
   - 7-column CSV with "derechos" → normalized to "DERECHOS"
   - 7-column CSV with accent variants ("Acciónes") → parse error with clear message
   - 7-column CSV with empty Tipo cell → default to "ACCIONES" (no warning)
   - 7-column CSV with invalid Tipo → parse error

#### Phase 2: Ledger Model & Persistence

1. **File:** `backend/src/portfolio/models.py`
   - Add `sales_type: Optional[str] = None` to `LedgerMovement` Pydantic model
   - Add `is_rights_sale: Optional[bool] = None` as a computed field (read-only)

2. **File:** `backend/src/portfolio/import_service.py` → `_row_to_movement()`
   - When `fmt == "sales"`:
     - Extract `row.get("sales_type", "ACCIONES")` (default if missing for backward compat)
     - Set `movement["sales_type"] = sales_type`
     - Set `movement["is_rights_sale"] = (sales_type == "DERECHOS")`

3. **Test coverage:**
   - Parse 6-column CSV → movements have `sales_type="ACCIONES"`, `is_rights_sale=False`
   - Parse 7-column CSV (Derechos) → movements have `sales_type="DERECHOS"`, `is_rights_sale=True`
   - Committed movement document contains both fields

#### Phase 3: Holdings Computation

1. **File:** `backend/src/portfolio/holdings_service.py`
   - In `compute_holdings()`, when processing SELL movements:
     ```python
     elif txn_type == "SELL":
         sales_type = m.get("sales_type", "ACCIONES")
         if sales_type == "ACCIONES":
             agg["total_shares"] -= qty
         # else: DERECHOS — do NOT decrement
         agg["total_sales_eur"] += gross_eur - commission_eur  # Both types count
     ```

2. **Test coverage:**
   - BUY 100, SELL 30 (ACCIONES) → holdings = 70
   - BUY 100, SELL 30 (ACCIONES), SELL 50 (DERECHOS) → holdings = 70 (DERECHOS not subtracted)
   - `total_sales_eur` includes both ACCIONES and DERECHOS sales proceeds
   - Existing data without `sales_type` defaults to "ACCIONES" behavior

#### Phase 4: API Response

1. **File:** `backend/web/portfolio_routes.py`
   - Ensure `LedgerMovement` is serialized with `sales_type` and `is_rights_sale` fields
   - No logic changes needed (just return the fields from the model)

2. **Test coverage:**
   - GET `/api/portfolio/movements` includes `sales_type` for SELL rows
   - GET `/api/portfolio/holdings` computes `total_shares` correctly with mixed sales types

---

### For Basher (Testing Requirements)

#### Unit Tests

**File:** `backend/tests/test_portfolio_parsers.py`

1. **Sales parser extension:**
   ```python
   def test_sales_parse_6_column_defaults_to_acciones():
       # 6-column CSV (no Tipo) → all rows default to "ACCIONES"
       
   def test_sales_parse_7_column_acciones():
       # Tipo = "Acciones" (various cases) → normalized to "ACCIONES"
       
   def test_sales_parse_7_column_derechos():
       # Tipo = "Derechos" (various cases) → normalized to "DERECHOS"
       
   def test_sales_parse_invalid_tipo_raises_error():
       # Tipo = "Invalid" → ValueError with clear message
       
   def test_sales_parse_empty_tipo_defaults_acciones():
       # Tipo column present but empty → defaults to "ACCIONES"
       
   def test_sales_parse_mixed_tipos():
       # Some rows ACCIONES, some DERECHOS → all parsed correctly
       
   def test_sales_parse_warnings_derechos_with_qty():
       # DERECHOS sale with quantity > 0 → warning DERECHOS_WITH_QUANTITY
       
   def test_sales_parse_warnings_acciones_zero_qty():
       # ACCIONES sale with quantity == 0 → warning ACCIONES_ZERO_QUANTITY
   ```

**File:** `backend/tests/test_portfolio_holdings.py`

2. **Holdings computation with mixed sales types:**
   ```python
   def test_holdings_derechos_sale_does_not_decrement():
       # BUY 100, SELL 30 (DERECHOS) → total_shares = 100
       
   def test_holdings_acciones_sale_decrements():
       # BUY 100, SELL 30 (ACCIONES) → total_shares = 70
       
   def test_holdings_mixed_sales_types():
       # BUY 100, SELL 30 (ACCIONES), SELL 20 (DERECHOS) → total_shares = 70
       
   def test_holdings_total_sales_includes_both_types():
       # SELL 30 (ACCIONES, €600), SELL 20 (DERECHOS, €400) → total_sales_eur = €990 (after fees)
       
   def test_holdings_backward_compat_missing_sales_type():
       # Movement without sales_type field → defaults to ACCIONES behavior
   ```

**File:** `backend/tests/test_portfolio_import_service.py`

3. **End-to-end import session:**
   ```python
   def test_import_sales_6_column_csv():
       # Upload 6-column sales CSV → all movements have sales_type="ACCIONES"
       
   def test_import_sales_7_column_mixed():
       # Upload 7-column sales CSV with mix of types → correct sales_type on each movement
       
   def test_import_session_preview_derechos_warnings():
       # Preview shows warnings for DERECHOS_WITH_QUANTITY, etc.
       
   def test_import_commit_derechos_sale():
       # Commit DERECHOS sale → movement is stored with correct sales_type
       # Holdings do NOT decrement for this sale
   ```

#### Integration Tests

4. **API responses:**
   ```python
   def test_api_get_movements_includes_sales_type():
       # GET /api/portfolio/movements → SELL rows have sales_type field
       
   def test_api_get_holdings_derechos_impact():
       # GET /api/portfolio/holdings → total_shares ignores DERECHOS sales
   ```

#### Test Fixtures

- Sample 6-column sales CSV
- Sample 7-column sales CSV with "Acciones" and "Derechos" rows
- Sample 7-column with invalid Tipo values
- Sample 7-column with empty Tipo cells

---

## Risks & Mitigation

| Risk | Impact | Mitigation |
|------|--------|-----------|
| **Backward compat break:** Existing 6-column CSVs rejected | High | Default 6-column to "ACCIONES"; no validation error. Tested. |
| **Silent incorrect behavior:** DERECHOS sales incorrectly decrement | High | Thorough unit + integration tests; clear warnings in preview. |
| **Data loss:** Updating Cosmos documents loses `sales_type` field | Medium | No updates needed for existing docs; read-time defaults are safe. |
| **Duplicate detection fails:** Same security sold twice (once each type) | Low | Duplicate hash excludes `sales_type`; this is correct (data issue, not system bug). |
| **User confusion:** DERECHOS sales look like normal SELL in UI | Medium | Preview badge "Derechos (no share impact)" clarifies; holdings summary confirms. |

---

## Deliverables

### By Livingston:
1. ✅ Parser extension: 6/7-column support, normalization, validation, warnings
2. ✅ Ledger model: `sales_type`, `is_rights_sale` fields
3. ✅ Holdings computation: DERECHOS sales do NOT decrement
4. ✅ API serialization: include new fields in movement responses
5. ✅ All changes backward-compatible; no data migration

### By Basher:
1. ✅ Unit tests: parser, holdings, import service (per above)
2. ✅ Integration tests: API, full import flow
3. ✅ Fixtures: 6/7-column CSVs, edge cases
4. ✅ Test coverage: >95% for new code paths

### By Danny (Review):
1. ✅ This document: design review and implementation guidance
2. ✅ Approval gate: review implementation PRs
3. ✅ Decision record: this document (frozen after sign-off)

---

## Related Decisions

- `danny-portfolio-implementation-contract.md` (v1.1, current contract)
- `danny-portfolio-second-rejection-resolution.md` (recent corrections)
- `danny-unified-security-master.md` (security catalog)

## Decision Log

| Date | Author | Status | Note |
|------|--------|--------|------|
| 2026-09-06 | Danny | DESIGN REVIEW | Initial design; awaiting implementation |

