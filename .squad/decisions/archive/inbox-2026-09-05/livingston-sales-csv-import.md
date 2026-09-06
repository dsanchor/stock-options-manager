# Sales History CSV Import Contract

**Author:** Livingston (Persistence & Integration Engineer)  
**Date:** 2026-09-05  
**Status:** DESIGN ONLY — Phase 1b extension alongside the dividend and purchase CSV import contracts. No production code. No user financial data stored or reproduced.  
**Directives:** `copilot-directive-20260905T170523+0200.md`, `copilot-directive-20260905T154228+0200.md`  
**Depends on:**  
- `livingston-dividend-csv-import.md` — base import architecture, `_unassigned` partition, alias map, dedup framework, account-optional policy  
- `livingston-purchase-csv-import.md` — precision rules, arithmetic checks, holdings derivation principles  
- `livingston-scrip-rights-topup-clarification.md` — `ca_event`/`ca_leg` model

---

## 1. Scope

This document defines the import contract for the user's sale history CSV/Excel file. It shares all infrastructure from the dividend and purchase import contracts (same `portfolio_ledger` container, same `_unassigned` partition, same alias map, same dedup framework, same account-optional policy). Only differences and sales-specific concerns are specified here.

Key new concerns:
- 6-column format with **no source unit sale price** — derived price is clearly labeled as CALCULATED, not a source fact
- Holdings replay under incomplete history: sales that predate imported purchases produce a structured inventory warning, not a destructive rejection
- Realized gain is UNAVAILABLE until complete acquisition history and a chosen cost-basis method exist; it is never fabricated or stored on the SELL document
- Import-order safety: selling before buying (from the import's perspective) is explicitly safe
- Realized-gain reproducibility across future cost-basis method changes
- Classification suggestions for tiny/odd proceeds; never auto-reclassification

**Not covered:** Withholding on gains (capital gains tax is a Phase 4 fiscal-export concern), short sales, options-related sells.

---

## 2. Input File Contract

### 2.1 Expected Columns (First Six Only)

Columns beyond position 5 are silently ignored. Column names are matched case-insensitively after whitespace trimming; positional index (0–5) is the tiebreaker.

| Position | Header (canonical) | Type | Currency | Notes |
|----------|-------------------|------|----------|-------|
| 0 | `Año` | Integer | — | Cross-check only; `Fecha venta` is authoritative |
| 1 | `Empresa` | String | — | Free-text company name; alias map (§5) |
| 2 | `Fecha venta` | Date | — | Sale/trade date. DD/MM/YYYY |
| 3 | `Acciones` | Decimal | — | Quantity sold. Supports fractional values. Positive in source; stored positive with direction in `txn_type: SELL` |
| 4 | `Comisión` | Decimal | **EUR** | Commission fee. Contextually EUR (consistent with purchase sheet) |
| 5 | `Total Venta` | Decimal | **EUR** | Gross sale proceeds before commission. Source fact |

**Currency is explicit and uniform:** both monetary columns are EUR. There is no per-share sale price column and no foreign-currency ambiguity to detect. The `WARNING_POSSIBLE_FX_MISMATCH` from the purchase import has no equivalent here.

**Shared parser:** format detection, BOM stripping, Spanish locale number parsing (comma decimal, period thousands), and DD/MM/YYYY date parsing are identical to the purchase and dividend import contracts.

### 2.2 Precision

Fractional sell quantities arise from broker automatic sales of fractional entitlements (e.g., selling 0.054 residual rights, or selling a fractional share received from a DRIP). The same 10-decimal-place string storage used for purchase quantities applies:

| Field | Storage precision | Notes |
|-------|------------------|-------|
| `Acciones` | 10 decimal places | Fractional share quantities; repeating decimals stored as-parsed |
| `Total Venta` | 6 decimal places | EUR; broker typically reports 2–4dp |
| `Comisión` | 6 decimal places | EUR |
| `derived_price_eur` | 10 decimal places | CALCULATED field; higher precision to minimize rounding chain errors |

---

## 3. Field Mapping and Arithmetic

### 3.1 What `Total Venta` Means

`Total Venta` is the **gross sale proceeds** — the amount the broker received from the market for selling the specified quantity of shares, before deducting commission. This is a parallel to `Total (€)` in the purchase sheet (which was principal before commission).

**Cash flow for a SELL:**
```
net_cash_eur = Total Venta - Comisión          (cash actually received by investor)
```

This is always `gross - fees` for a SELL (cash in, fees subtracted). Compare to BUY where `net_cash_eur = Total + Comisión` (cash out, fees added).

### 3.2 Derived Unit Sale Price — Provenance Rule

The source file contains no per-share sale price. Any unit price is a mathematical derivation:

```
derived_price_eur = Total Venta / Acciones
```

This **must not** be stored in `price_txn` (which is reserved for source facts). It is stored in a separate field with explicit provenance labeling:

```jsonc
{
  "price_txn": null,
  "price_txn_source": null,
  // price_txn is null for imported SELL records from this format.
  // There is no source unit price.

  "derived_price_eur": "182.5000000000",
  "derived_price_eur_provenance": "CALCULATED",
  "derived_price_eur_note": "Derived as Total Venta / Acciones. Not a source fact from the broker statement. Do not use for tax or audit purposes without verification."
}
```

`CALCULATED` is a provenance enum value that signals "no external source — this value was arithmetically derived within the import pipeline." It is visually distinguished in the UI from source-fact prices (e.g., displayed in italic or with a `ƒ` derived-value indicator rather than the raw value).

**The derived price is never used in any financial calculation within the ledger.** It is informational only. All monetary calculations use `Total Venta` (gross_eur) and `Comisión` (fees_eur) as the two authoritative EUR facts.

### 3.3 SELL `ledger_txn` Schema

```jsonc
{
  "id": "txn_{account_id}_{YYYYMMDD}_{empresa_norm_hash}_SELL_{seq:05d}",
  "account_id": "_unassigned",    // or real account
  "doc_type": "ledger_txn",
  "txn_type": "SELL",

  "security": { /* from alias map, or null if unresolved */ },
  "trade_date": "2024-06-15",
  "settlement_date": null,         // not in file

  "quantity": "150.0000000000",    // 10 dp; always positive; direction = SELL
  "quantity_unit": "shares",

  // No source unit price:
  "price_txn": null,
  "price_txn_source": null,
  "derived_price_eur": "182.5000000000",
  "derived_price_eur_provenance": "CALCULATED",
  "derived_price_eur_note": "Derived as Total Venta / Acciones. Not a source fact.",

  "txn_currency": "EUR",

  // Source facts (always EUR):
  "gross_txn": "27375.000000",    // = Total Venta from file
  "gross_eur": "27375.000000",    // = gross_txn (all EUR)

  "fees": {
    "total_txn": "9.950000",      // = Comisión from file — source fact
    "total_eur": "9.950000",
    "breakdown": [
      { "label": "commission", "amount_txn": "9.950000", "amount_eur": "9.950000" }
    ]
  },

  // Derived cash-in:
  "net_txn": "27365.050000",      // = gross_txn - fees.total_txn
  "net_eur": "27365.050000",

  "fx": {
    "rate_convention": "EUR_PER_TXN_CCY",
    "rate": "1.000000000",
    "rate_source": "EXACT",       // everything already EUR
    "rate_date": "2024-06-15",
    "ecb_reference_rate": null,
    "original_rate": null
  },

  // Realized gain: never stored, always computed on-read. See §6.
  // "realized_gain_eur": absent   ← this field does not exist on the document

  "inventory_status": "OK",
  // OK | WARNING_INVENTORY_DEFICIT
  // Set by post-import holdings replay. If replay shows holdings go negative
  // at this sell's trade_date, set to WARNING_INVENTORY_DEFICIT.
  // This field is mutable (only field that can be updated without a void+correction,
  // because it is computed from ledger state, not from the source file).

  "import_source": "csv_import",
  "import_provenance": { /* see §8 */ },
  "assignment_status": "UNASSIGNED",
  "assigned_account_id": null,
  "revision": 0,
  "status": "active"
}
```

### 3.4 Net Cash Sign Convention

`net_eur` on a SELL record represents **cash received** (an inflow). It is stored as a **positive number**. The direction (inflow) is conveyed by `txn_type: SELL`, not by a negative sign. This is consistent with the convention across all document types: amounts are always positive; direction is in the type/classification field.

If `Comisión > Total Venta` (commission exceeds proceeds), `net_eur` would be negative. This unusual case produces `WARNING_COMMISSION_EXCEEDS_PROCEEDS` (§7.5) and the negative net is stored as-is — it is the mathematically correct derived value from the source facts.

---

## 4. Holdings Replay and Inventory Deficit

### 4.1 The Import-Order Problem

The user's three CSV files (purchases, dividends, sales) may be imported in any order. Specifically, sales may be imported before the corresponding purchases. From the system's perspective at import time, a 2018 SELL of 200 shares of Iberdrola may arrive before any Iberdrola BUY record exists in the ledger.

**Decision: Import-order safety is guaranteed. Selling before buying (from import's perspective) is explicitly valid.**

The system never rejects a SELL record because the acquisition history is absent or incomplete. Historical data is incomplete by definition during the import process. Enforcing inventory non-negativity during import would make it impossible to load partial history.

### 4.2 Holdings Derivation (Recap)

Holdings for `(account_id, isin)` are always computed on-read by replaying all active ledger records chronologically:

```
running_qty = 0
for record in chronological_order(BUY + SELL + SHARE_ACQUISITION confirmed):
    if BUY:         running_qty += record.quantity
    if SELL:        running_qty -= record.quantity
    if SHARE_ACQ:   running_qty += record.quantity
    if running_qty < 0:
        flag this SELL record with WARNING_INVENTORY_DEFICIT
```

The replay is always re-run from scratch using whatever records are currently in the ledger. It is not persisted. There is no checkpoint or snapshot that could become stale.

### 4.3 `WARNING_INVENTORY_DEFICIT`

When the replay identifies a chronological point where holdings go negative, the SELL records that caused the dip are tagged:

```
ledger_txn.inventory_status = "WARNING_INVENTORY_DEFICIT"
```

Properties of this warning:
- **Non-destructive:** The SELL record is fully imported and fully valid. It participates in all aggregates (total proceeds, net cash, etc.).
- **Mutable:** `inventory_status` is the only field on a `ledger_txn` that can be updated without a void+correction cycle. This is because it is computed from ledger state (not a source fact), and it must update automatically as new purchase records are added.
- **Not in `import_provenance.import_status[]`:** It is a field on the document itself, not a data-quality code. It reflects a derived ledger state.
- **Not a warning badge in the import summary:** It is reported in a separate "Inventory" section of the reconciliation UI.

**`inventory_status` update trigger:** Whenever new BUY or SHARE_ACQUISITION records are added to the ledger for a given `(account_id, isin)`, a post-write re-replay for that pair identifies all SELL records that previously had `WARNING_INVENTORY_DEFICIT` and clears those that are now covered. This is a lightweight operation (one partition, one security, chronological scan).

### 4.4 Dashboard Behavior with Inventory Deficit

**Securities page (`/portfolio/securities`):**

| Column | Behavior when deficit exists |
|--------|----------------------------|
| Quantity | Shows actual derived quantity (may be negative). Not hidden or zeroed. |
| Avg cost/share | Shows computed value from available purchases; marked "(incomplete history)" |
| Cost basis status | `HISTORY_INCOMPLETE` — informational label |
| Visual indicator | Amber row highlight; tooltip: "This security has sells that precede recorded purchases. Import the full purchase history to resolve." |

A negative holding quantity on the Securities page is not an error state — it is accurate given the available data. The user understands their full history is not yet loaded.

**Movements page:** SELL records with `inventory_status: WARNING_INVENTORY_DEFICIT` show an amber indicator in the row. The detail panel explains: "At the time of this sale, the recorded purchase history does not cover the full quantity sold. This will resolve when purchase records are imported."

**Realized Gain column:** Always "—" when `inventory_status: WARNING_INVENTORY_DEFICIT`. See §6.

### 4.5 Self-Healing on New Purchase Import

When purchases are imported after sales:
1. New BUY `ledger_txn` documents are written.
2. A post-import holdings replay for each affected `(account_id, isin)` runs.
3. SELL records where the deficit is now covered are updated: `inventory_status = "OK"`.
4. SELL records still with insufficient coverage retain `WARNING_INVENTORY_DEFICIT`.

This is automatic — no user action required to "reconcile" sells against newly imported purchases. The ledger-first, on-read derivation model makes this self-healing property possible.

---

## 5. Realized Gain — Always UNAVAILABLE Until Explicitly Computable

### 5.1 The Non-Storage Rule

**`realized_gain_eur` is never stored on the SELL `ledger_txn` document.** Ever. This is not deferred — it is a permanent design decision.

Reasons:
- Gain depends on cost basis, which depends on the full acquisition history. If a new purchase is imported (filling a historical gap), the realized gain changes. A stored value would become stale silently.
- The cost-basis method is not yet chosen (average cost is the MVP default but FIFO is planned for Phase 3). Storing a gain with an implicit method assumption creates an irreversible dependency on that method.
- Tax-relevant gain calculations must be reproducible with explicit parameters (method, date, lot assignment). A stored value loses those parameters.
- Gain is a derived value, not a source fact. The ledger-first principle prohibits storing derived primary facts.

### 5.2 Conditions for On-Read Gain Computation

Realized gain is computed on-read for a SELL record only when ALL of the following are true:

| Condition | When not met — display |
|-----------|----------------------|
| `inventory_status: OK` (no deficit) | "—" (Incomplete history) |
| All active BUY and SHARE_ACQUISITION records for `(account_id, isin)` up to `trade_date` are present | Cannot determine — no way to know if history is complete |
| `cost_basis.recorded_method` is set on all SHARE_ACQUISITION legs contributing to the holding | "—" (Pending cost basis) |
| A cost-basis method is configured (average cost for MVP) | "—" (Method not configured) |

**The system cannot verify that all purchases are present** (the ledger does not know what records are missing — it only knows what is there). Therefore, the second condition above is a user confirmation, not an automated check. A UI toggle per security — "Mark purchase history as complete" — serves as the user's explicit assertion that the ledger is complete for that security. Until toggled: gain is "—".

This `purchase_history_complete` flag is stored on the `security_alias_map` entry or on a per-`(account_id, isin)` record (design choice for Phase 3 detail).

### 5.3 Average-Cost Gain Computation (MVP)

Under average cost (Spanish default):

```
avg_cost_eur_at_sale = SUM(gross_eur + fees_eur for BUY records up to trade_date)
                     / SUM(quantity for BUY records up to trade_date)
                    [+ classified SHARE_ACQUISITION records with known cost_per_share_eur]

cost_of_shares_sold = avg_cost_eur_at_sale × sell.quantity
realized_gain_eur   = sell.net_eur - cost_of_shares_sold
```

This is computed by replaying the full ledger up to `trade_date` for the security. It is returned as a computed field in API responses but **never stored** on the SELL document.

### 5.4 Historical Reproducibility

**Problem:** Once Phase 3 introduces FIFO lot assignment, the average-cost gain for historical sells may differ from the FIFO gain. If both methods exist simultaneously, which is "correct"?

**Design:** Realized gain is always computed with the **currently active method** for the account. The user selects a cost-basis method per account (not per security). Changing the method retroactively recalculates all gains for that account from the full ledger.

**For tax reporting (Phase 4):** Once a tax return is filed for a given year, the gain calculations used must be frozen. A `gain_calculation_snapshot` document (created at declaration time) preserves the computed gain per SELL, the method used, and the cost basis inputs, all linked to the tax year and declaration reference. This snapshot is additive and separate — it does not modify the SELL document.

**Until Phase 4:** No gain is stored anywhere. Reproducibility is achieved by replaying the same immutable ledger with the same method — the result is deterministic.

---

## 6. Validation Checks

All standard checks from the dividend and purchase contracts apply. Sales-specific additions:

### 6.1 Year vs. Date Cross-Check
`WARNING_YEAR_DATE_MISMATCH` if `Año ≠ year(Fecha venta)`. Same as other importers.

### 6.2 Zero Proceeds
```
IF Total Venta == 0 AND Acciones > 0
    → WARNING_ZERO_PROCEEDS
```
A sale with zero gross proceeds and positive quantity is unusual (a gift of shares, a write-off, a data gap). Imported as a valid SELL at €0. Holdings quantity is reduced by `Acciones`. Net cash = 0 − Comisión (negative if fee exists). User reviews.

### 6.3 Commission Exceeds Proceeds
```
IF Comisión > Total Venta + 0.02  (tolerance for rounding)
    → WARNING_COMMISSION_EXCEEDS_PROCEEDS
```
Net cash would be negative — possible for a tiny sale with a large fixed commission. Imported. Stored net_eur is the correct derived value (negative). Informational; user reviews.

### 6.4 Zero Quantity
```
IF Acciones == 0
    → ERROR_ZERO_QUANTITY  (row not written)
```
A sale of zero shares has no meaning. Unlike zero-cost purchase rows (which have a distinct scrip interpretation), a zero-quantity sell has no valid interpretation.

### 6.5 Negative Values
```
IF ANY of {Acciones, Total Venta, Comisión} < 0
    → ERROR_NEGATIVE_AMOUNT  (row not written)
```
All source values are positive; direction is in `txn_type`.

### 6.6 All-Zero Row
```
IF Acciones == 0 AND Total Venta == 0 AND Comisión == 0
    → SKIPPED_ALL_ZERO
```

### 6.7 Commission Without Proceeds
```
IF Comisión > 0 AND Total Venta == 0 AND Acciones == 0
    → ERROR_FEE_WITHOUT_PROCEEDS  (row not written)
```

### 6.8 Unresolved Security
`WARNING_SECURITY_UNRESOLVED` — same as dividend and purchase imports. Alias map lookup (§5); null ISIN stored; no ticker/ISIN guessed.

### 6.9 Status and Warning Code Reference

**Data-quality codes (in `import_provenance.import_status[]`):**

| Code | Level | Written | Action |
|------|-------|---------|--------|
| `IMPORTED_CLEAN` | Info | Yes | None |
| `WARNING_SECURITY_UNRESOLVED` | Warning | Yes (null ISIN) | Map company name |
| `WARNING_YEAR_DATE_MISMATCH` | Warning | Yes | Confirm date |
| `WARNING_ZERO_PROCEEDS` | Warning | Yes | Review — intentional or data gap? |
| `WARNING_COMMISSION_EXCEEDS_PROCEEDS` | Warning | Yes | Verify statement |
| `WARNING_POSSIBLE_DUPLICATE` | Warning | Yes | Confirm or void |
| `WARNING_AMBIGUOUS_NUMBER` | Warning | Yes (best-parse) | Confirm value |
| `SKIPPED_ALL_ZERO` | Info | No | None |
| `SKIPPED_DUPLICATE` | Info | No | None |
| `ERROR_ZERO_QUANTITY` | Error | No | Fix source |
| `ERROR_NEGATIVE_AMOUNT` | Error | No | Fix source |
| `ERROR_INVALID_DATE` | Error | No | Fix source |
| `ERROR_MISSING_DATE` | Error | No | Fix source |
| `ERROR_INVALID_NUMBER` | Error | No | Fix source |
| `ERROR_FEE_WITHOUT_PROCEEDS` | Error | No | Fix source |
| `ERROR_NO_HEADER_DETECTED` | Error (batch) | Nothing | Fix file |

**Ledger-state field (on the document itself, NOT in import_status, NOT in badge counts):**

| Field | Values | Meaning |
|-------|--------|---------|
| `inventory_status` | `OK` \| `WARNING_INVENTORY_DEFICIT` | Whether holdings replay shows sufficient quantity at this sell date |

---

## 7. Corporate-Action-Like Small Proceeds — Classification Suggestion

### 7.1 The Pattern (Synthetic Examples)

```
Empresa: Unilever  |  Acciones: 0.054  |  Total Venta: €1.34  |  Comisión: €0.00
Empresa: Iberdrola |  Acciones: 0.600  |  Total Venta: €3.12  |  Comisión: €0.00
```

Small fractional quantities with tiny proceeds and zero commission may be broker-automated sales of:
- Residual fractional rights from a scrip dividend
- Fractional shares from a DRIP
- Automatic fractional cash-out

These are not ordinary market sales. However, they could also legitimately be small partial sells.

### 7.2 Classification Suggestion Criteria

An informational suggestion is produced (not a warning, not a block) when **all** of:
- `Acciones < 1.0` (fractional quantity)
- `Total Venta < configurable_threshold` (default: €15.00, user-configurable per account)
- `Comisión == 0` (broker-automated, no commission)

Suggestion stored in `import_provenance.classification_suggestion`:
```jsonc
{
  "type": "POSSIBLE_RIGHTS_SALE_OR_FRACTIONAL_BROKER_ACTION",
  "criteria_matched": ["fractional_quantity", "small_proceeds", "zero_commission"],
  "outcome": null   // null | RECLASSIFIED_AS_RIGHTS_SOLD | CONFIRMED_AS_SELL
}
```

### 7.3 Reclassification Path (User-Initiated)

If the user confirms this is a residual rights sale:
1. The `ledger_txn` (SELL) is voided.
2. A `RIGHTS_SOLD` `ca_leg` is created (linked to the matching dividend `ca_event` if the user identifies it, or linked to a new standalone `ca_event` otherwise).
3. The reclassified leg carries its own withholding fields (origin + destination) — the user fills these if they know the applicable rates.
4. `import_provenance.classification_suggestion.outcome = RECLASSIFIED_AS_RIGHTS_SOLD`.

If the user confirms it is an ordinary sell:
- `outcome = CONFIRMED_AS_SELL`. Suggestion dismissed; does not re-surface.

**Auto-reclassification:** never. Every reclassification is an explicit user action.

---

## 8. Deduplication and Idempotency

### 8.1 Semantic Dedup Key

```
{account_id}|{empresa_normalized}|{sale_date}|{quantity_6dp}|{total_venta_2dp}
```

Including `quantity_6dp` handles legitimate same-day split sales (different quantities):

```
Synthetic example:
2024-06-15 | Iberdrola | 100 shares | Total: €1,234.00 | Fee: €9.95   ← first tranche
2024-06-15 | Iberdrola | 50 shares  | Total: €617.50   | Fee: €9.95   ← second tranche
```
Different quantities → different semantic keys → both imported cleanly.

Same quantity, same total, same day → same semantic key → `WARNING_POSSIBLE_DUPLICATE`. User decides.

### 8.2 Row Hash (Level 1)

SHA-256 of the normalized raw row string. Checked across both the target partition and `_unassigned` partition (cross-partition query). Identical to purchase and dividend import behavior.

### 8.3 Cross-Import Consideration

A tiny SELL that was auto-reclassified as a `RIGHTS_SOLD` leg: after reclassification, the original `ledger_txn` is voided. If the same file is re-imported, the row hash is present in the voided document — it is NOT treated as a duplicate (voided records are excluded from the duplicate check, same as the purchase contract).

---

## 9. Import Order Safety Across All Three Importers

### 9.1 Any Order Is Valid

The three CSV files may be imported in any combination and order:

| Import order | Result |
|-------------|--------|
| Dividends only | Clean dividend events; no holdings quantity (no BUYs/SELLs) |
| Purchases only | Clean holdings and cost basis |
| Sales only | SELL records with `WARNING_INVENTORY_DEFICIT` on all rows (no purchase history) |
| Purchases → Sales | Holdings correct; realized gain computable once method configured and history marked complete |
| Sales → Purchases | SELL records initially have `WARNING_INVENTORY_DEFICIT`; re-resolved automatically when purchases arrive |
| All three in any order | Final state is identical regardless of import order, since holdings are always derived on-read |

**Determinism guarantee:** The final ledger state (holdings quantity, net cash, all source facts) is identical regardless of import order. The only time-varying fields are `inventory_status` (resolved when history fills in) and eventually computed realized gains (available once history is marked complete).

### 9.2 Recomputation Strategy

There is no explicit recomputation step. Holdings and gain are computed on-read from the current ledger state. Adding new records changes the computation automatically. The system does not need to be told "recompute now" — every read of the holdings page runs the replay.

**Exception: `inventory_status` field.** This field on SELL documents is materialized (stored) to avoid re-running a full historical replay on every read. It is invalidated and re-run after any BUY import for the affected `(account_id, isin)`. The invalidation is a post-write trigger: write BUY → trigger replay for that security → update `inventory_status` on affected SELL documents.

This is the only case where an import of one document type triggers updates to documents of another type. It is bounded: affects only the same `(account_id, isin)` pair, only SELL documents, only `inventory_status` field (one field, no void+correction cycle).

---

## 10. Provenance — Sales Import Specifics

The `import_provenance` subobject follows the standard structure from the dividend and purchase contracts, with these additions:

```jsonc
"import_provenance": {
  // ... all standard fields (batch_id, csv_row_number, row_sha256,
  //     empresa_raw, empresa_normalized, csv_año, import_status,
  //     raw_row [opt-in]) ...

  "raw_total_venta": "27375.000000",    // raw parsed Total Venta; source fact
  "raw_comisión": "9.950000",           // raw parsed Comisión; source fact
  "raw_acciones": "150.0000000000",     // raw parsed quantity

  "computed_derived_price": "182.5000000000",
  // = raw_total_venta / raw_acciones; labeled CALCULATED.
  // Stored in provenance alongside the document field for audit.

  "classification_suggestion": {
    "type": null,   // "POSSIBLE_RIGHTS_SALE_OR_FRACTIONAL_BROKER_ACTION" if triggered
    "criteria_matched": [],
    "outcome": null
  }
}
```

---

## 11. Unified Import Batch Model

All three importers (dividend, purchase, sale) write `import_batch` documents to `portfolio_ledger` using the same schema. A new field distinguishes the source:

```jsonc
{
  "doc_type": "import_batch",
  "import_type": "SALE",     // DIVIDEND | PURCHASE | SALE | MIXED
  // MIXED: a future single-file format containing multiple types

  "summary": {
    "rows_total": 45,
    "rows_imported_clean": 38,
    "rows_with_warnings": 5,
    "rows_skipped": 1,
    "rows_error": 1,

    "warnings_breakdown": {
      "WARNING_SECURITY_UNRESOLVED": 3,
      "WARNING_YEAR_DATE_MISMATCH": 1,
      "WARNING_COMMISSION_EXCEEDS_PROCEEDS": 1
    },

    "inventory_status_breakdown": {
      // Separate section; inventory deficit is NOT in warnings_breakdown.
      "WARNING_INVENTORY_DEFICIT": 8   // 8 sell rows where history is incomplete at import time
    },

    "errors_breakdown": {
      "ERROR_ZERO_QUANTITY": 1
    }
  }
}
```

The `inventory_status_breakdown` counter in the batch summary is informational and updates over time (as purchases are imported and deficits resolve). It is best-effort — a background job maintains it; the batch document is not the authoritative source for current deficit count.

---

## 12. API Additions

New endpoints for the sales importer, following the `/api/portfolio/` BFF pattern:

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/portfolio/sales/import` | Accept sale CSV/Excel upload; return batch id and summary |
| `GET` | `/api/portfolio/sales/inventory-review?year=2026` | Year-filtered list of SELL records with `inventory_status: WARNING_INVENTORY_DEFICIT` |
| `POST` | `/api/portfolio/ledger-txn/{id}/reclassify-rights` | Reclassify a tiny SELL as a `RIGHTS_SOLD` `ca_leg` |
| `GET` | `/api/portfolio/securities/{isin}/gain?account_id=X` | On-read realized gain for all SELLs of this security; returns null fields when history incomplete |
| `POST` | `/api/portfolio/securities/{isin}/mark-history-complete` | User asserts that purchase history for this security is complete for this account; enables gain computation |
| `GET` | `/api/portfolio/securities/{isin}/holdings-replay?account_id=X` | Debug endpoint: returns the full chronological ledger replay for a security, highlighting any deficit points |

Reused from purchase/dividend contracts (no changes needed):
- `/api/portfolio/accounts/assign` — account assignment
- `/api/portfolio/purchases/link-candidates` — for cross-import suggestions (can surface dividend events as potential parents of a reclassified rights sell)

---

## 13. Phase Placement

**Phase 1b alongside dividend and purchase CSV imports.** All three importers share the same container, same partition design, same alias map, and the same batch infrastructure. The sale importer introduces no new infrastructure dependencies beyond what the purchase importer already requires.

The `inventory_status` post-write trigger (§9.2) is the only cross-document write introduced by the sale importer. It operates within a single `(account_id, isin)` partition and does not require cross-partition transactions.

**Prerequisites for the sale importer** (identical to purchase importer):
- `portfolio_ledger` container provisioned
- `ca_event`/`ca_leg` write path available (needed only if classification suggestions are acted on)
- `security_alias_map` seeded

**Recommended import sequence for the initial migration:**
1. Import purchases → establishes holdings and cost basis anchor
2. Import dividends → overlays dividend history; cross-import suggestions can appear immediately
3. Import sales → inventory replay runs; most `WARNING_INVENTORY_DEFICIT` flags resolved if step 1 was imported first
4. Alternatively: import all three simultaneously (same result; self-healing as described in §9)

The recommended sequence is a convenience hint, not a constraint. Any order produces a correct final state.

---

## 14. Open Questions

| # | Question | Impact |
|---|----------|--------|
| Q1 | Can the sale file contain negative quantities representing "bought to close" a short position? | Short sales are not in scope. A negative `Acciones` would currently trigger `ERROR_NEGATIVE_AMOUNT`. If the user has short sales, the row router needs a short-sell branch (deferred). |
| Q2 | Does the sale file ever include withholding columns (e.g., capital gains withholding for Spanish-resident investors)? | The current 6-column format has none. If a future version of the file gains a `Retención` column, it maps directly to `withholding.origin` on the SELL. No schema change needed — the withholding subobject already exists on `ledger_txn`. |
| Q3 | Is the `purchase_history_complete` flag per security per account, or per security globally? | Design assumes per `(account_id, isin)`. A security could be fully imported for HeyTrade but partially imported for ING — completeness is per account. |
| Q4 | For the `inventory_status` update trigger, should it run synchronously (blocking import response) or asynchronously? | Asynchronously is strongly preferred — a historical import of 500 purchase rows should not wait for 500 inventory replays. Background queue or post-import batch job. |

---

## 15. Summary

**6 columns:** `Año`, `Empresa`, `Fecha venta`, `Acciones`, `Comisión`, `Total Venta`. All monetary values explicitly EUR. No per-share sale price in the source.

**`Total Venta` = gross proceeds** (before commission). `net_cash_eur = Total Venta − Comisión`. Both source facts stored. Derived unit price stored separately with `provenance: CALCULATED` and never used in financial calculations.

**No source unit price, ever.** `price_txn = null` on all imported SELL records. `derived_price_eur` is a computed label — audit-transparent, UI-distinguished, excluded from all ledger arithmetic.

**Import-order safety:** Sales may be imported before purchases. SELL records with no acquisition history receive `inventory_status: WARNING_INVENTORY_DEFICIT` — a non-destructive ledger-state field, not a data-quality code. The deficit resolves automatically when purchases are imported (self-healing via on-read replay). No user action or re-import needed.

**Realized gain: never stored.** Computed on-read from the complete ledger using the active cost-basis method, only when: no inventory deficit, purchase history marked complete by user, all SHARE_ACQUISITION cost bases are resolved, and a method is configured. Otherwise: "—". Phase 4 fiscal export introduces `gain_calculation_snapshot` documents to freeze a computation for a filed tax year.

**Tiny/fractional sells** receive an informational classification suggestion (`POSSIBLE_RIGHTS_SALE_OR_FRACTIONAL_BROKER_ACTION`) when quantity < 1, proceeds < threshold, and commission = 0. Never auto-reclassified. User-confirmed reclassification converts to a `RIGHTS_SOLD` ca_leg.

**Same-day split sales** are legitimate: semantic dedup key includes quantity, preventing false positives.

**Phase 1b:** Runs alongside dividend and purchase imports. Same container, same alias map. One new behavioral addition: `inventory_status` post-write trigger updates affected SELL records when purchases arrive. Recommended but not required import order: purchases → dividends → sales.
