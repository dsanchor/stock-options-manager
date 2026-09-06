# Purchase History CSV Import Contract

**Author:** Livingston (Persistence & Integration Engineer)  
**Date:** 2026-09-05  
**Status:** DESIGN ONLY — extension of the Phase 1b dividend CSV import architecture. No production code. No user financial data stored or reproduced.  
**Directives:** `copilot-directive-20260905T170353+0200.md`, `copilot-directive-20260905T154228+0200.md`  
**Depends on:**  
- `livingston-dividend-csv-import.md` — base import architecture, `_unassigned` partition, alias map, dedup framework  
- `livingston-scrip-rights-topup-clarification.md` — `ca_event`/`ca_leg` model, `SHARE_ACQUISITION`, `CASH_TOP_UP`

---

## 1. Scope

This document defines the import contract for the user's purchase history CSV/Excel file. It is an extension — not a replacement — of the dividend CSV import architecture defined in `livingston-dividend-csv-import.md`. All infrastructure from that document (the `_unassigned` partition, the `security_alias_map`, the dedup framework, the account-optional policy, the `portfolio_ledger` container, and the reconciliation lifecycle) applies here unchanged unless explicitly noted.

New concerns specific to purchase records:
- 7-column format mapping and arithmetic invariants
- Currency semantics: `Total (€)` and `Comisión` are explicit EUR; `Valor compra` is a source fact whose currency is assumed EUR unless evidence suggests otherwise
- Precision rules for fractional share quantities and high-precision unit prices
- Zero-cost rows: `PENDING_SCRIP_CLASSIFICATION` state, not ordinary BUY
- Safe suggestion-only linking of zero-cost purchase rows to existing dividend events
- Paid rows near zero-cost rows: classification suggestion, never auto-conversion
- Holdings impact of unclassified zero-cost acquisitions
- Dedup handling for legitimate same-day same-company multiple purchases
- Phase 1b placement and API surface additions

**Not covered:** SELL history, corporate action history, IBKR Flex Query.

---

## 2. Input File Contract

### 2.1 Expected Columns (First Seven Only)

Columns beyond position 6 are silently ignored. Column names are matched case-insensitively after whitespace trimming; positional index (0–6) is the tiebreaker if header matching fails.

| Position | Header (canonical) | Type | Notes |
|----------|-------------------|------|-------|
| 0 | `Año` | Integer | Calendar year. Cross-check only; `Fecha compra` is authoritative. |
| 1 | `Empresa` | String | Free-text company name. NOT a ticker or ISIN. Subject to alias mapping (§6). |
| 2 | `Fecha compra` | Date | Purchase/trade date. Format `DD/MM/YYYY`. |
| 3 | `Valor compra` | Decimal | Unit price per share. Spanish locale (comma decimal). Source-provided EUR unless reconciliation reveals otherwise — see §4.3. |
| 4 | `Acciones` | Decimal | Share quantity purchased. Supports fractional values including repeating decimals. |
| 5 | `Total (€)` | Decimal | Principal amount. Column label explicitly `(€)` — always EUR. See §4.1 for semantics. |
| 6 | `Comisión` | Decimal | Commission/fee. Contextually EUR. |

**Shared parser from dividend import:** Format detection (xlsx/TSV/semicolon-CSV/comma-CSV), BOM stripping, Spanish locale number parsing (§2.3 of dividend contract), and date parsing (DD/MM/YYYY) are all reused without modification.

### 2.2 Precision Handling — Fractional Quantities and High-Precision Prices

The dividend import uses 6 decimal places for quantities and 9 for FX rates. Purchase records require higher precision in two fields:

| Field | Storage precision | Reason |
|-------|------------------|--------|
| `Acciones` (quantity) | **10 decimal places** | Fractional share quantities from DRIP/scrip can be irrational/repeating; 10dp preserves broker statement values to sufficient accuracy |
| `Valor compra` (unit price) | **10 decimal places** | High-precision unit prices (e.g., a security at €0.00123456789) require more than 6dp to avoid arithmetic drift |
| `Total (€)` | 6 decimal places | EUR principal; broker typically rounds to 2–4dp but store to 6 for consistency |
| `Comisión` | 6 decimal places | Fee in EUR; typically 2dp from broker |
| All computed amounts (`gross_eur`, `net_eur`, etc.) | 6 decimal places | Consistent with the base model |

**All values stored as decimal strings**, never IEEE 754 floats. Python `decimal.Decimal` with `ROUND_HALF_UP` is the computation context for all arithmetic. This rule is identical to the dividend import and must not be violated.

**Repeating decimal handling:** Broker statements may truncate a repeating decimal (e.g., `9.090909` instead of `9.0909̄`). The system stores whatever the file provides with up to 10dp. If the truncation creates a meaningful arithmetic error in the principal check (§4.2), the `WARNING_PRINCIPAL_MISMATCH` warning is produced. The raw value is stored; the user resolves by confirming or correcting. The system never attempts to expand a truncated decimal to its repeating form.

---

## 3. Batch-Level Metadata (Not in File)

Reuses the same batch parameter structure as the dividend import. Differences noted:

| Parameter | Applies to purchases? | Notes |
|-----------|----------------------|-------|
| `account_id` | Yes (optional) | Same `_unassigned` partition design. Missing broker/account generates no warning. |
| `source_currency` | **Modified** — see §4.3 | For purchases, `source_currency` applies to `Valor compra` specifically, not to Total/Commission which are always EUR. |
| `fx_behavior` | Modified — see §4.3 | |
| `alias_map_id` | Yes | Same alias map shared across both importers. |
| `store_raw_rows` | Yes | Same opt-in raw retention. |
| `batch_captures_dest_wht` | No | Not applicable to purchase records (no withholding). |

**New batch parameter: `price_currency`**

| Parameter | Field name | Description |
|-----------|-----------|-------------|
| Price column currency | `price_currency` | `EUR` \| `USD` \| `GBP` \| `CHF` \| `UNKNOWN`. The currency of the `Valor compra` column. Default: `EUR`. If the user knows all prices in this batch are in a foreign currency (e.g., the broker shows USD per-share prices while totaling in EUR), specify it here. See §4.3. |

---

## 4. Field Mapping and Arithmetic

### 4.1 What `Total (€)` Means — Source-Fact Semantics

Based on the observed column pattern (`Valor compra × Acciones ≈ Total (€)`), `Total (€)` represents the **principal amount** — the product of unit price and quantity — before any commission. This is the gross consideration, not the total cash outflow.

**The four source facts and their domain mappings:**

| Source column | Domain field | Currency | Notes |
|--------------|-------------|----------|-------|
| `Valor compra` | `price_txn` | `price_currency` (default EUR) | Unit price as stated in file |
| `Acciones` | `quantity` | — (shares) | Always positive |
| `Total (€)` | `gross_txn` / `gross_eur` | EUR (explicit) | Principal = price × qty (broker-rounded) |
| `Comisión` | `fees.total_txn` / `fees_eur` | EUR (contextual) | Commission; stored in `fees.breakdown` as `{label: "commission", amount_txn: "..."}` |

**Total cash outflow (net_txn):**
```
net_txn = Total(€) + Comisión          [for BUY: gross + fees]
net_eur = net_txn                       [since both are already EUR]
```

Both `Total (€)` and `Comisión` are stored as independent source facts. The `net_txn` is derived and also stored (for query performance) but is always re-derivable. If the arithmetic does not hold exactly, both facts are preserved — the discrepancy is flagged, not corrected.

**Schema for a paid BUY `ledger_txn`:**
```jsonc
{
  "doc_type": "ledger_txn",
  "txn_type": "BUY",

  "security": { /* from alias map or null if unresolved */ },
  "trade_date": "2024-01-15",
  "settlement_date": null,          // not in file

  "quantity": "150.0000000000",     // 10 dp
  "quantity_unit": "shares",

  "price_txn": "182.5000000000",    // 10 dp; source-provided value
  "price_currency": "EUR",          // or USD/GBP/CHF if batch overrides

  "txn_currency": "EUR",
  // txn_currency = price_currency when amounts are homogeneous.
  // When price_currency ≠ EUR (foreign security), see §4.3.

  "gross_txn": "27375.000000",      // = Total(€) from file — source fact
  "gross_eur": "27375.000000",      // = gross_txn (since Total always EUR)

  "fees": {
    "total_txn": "9.950000",        // = Comisión from file — source fact
    "total_eur": "9.950000",
    "breakdown": [
      { "label": "commission", "amount_txn": "9.950000", "amount_eur": "9.950000" }
    ]
  },

  "net_txn": "27384.950000",        // = gross_txn + fees.total_txn (BUY: cash out)
  "net_eur": "27384.950000",

  "fx": {
    "rate_convention": "EUR_PER_TXN_CCY",
    "rate": "1.000000000",          // 1.0 when everything is EUR
    "rate_source": "EXACT",         // EXACT = txn_currency is EUR, no conversion
    "rate_date": "2024-01-15",
    "ecb_reference_rate": null,
    "original_rate": null
  },

  "import_source": "csv_import",
  "import_provenance": { /* see §9 */ },
  "assignment_status": "UNASSIGNED",
  "assigned_account_id": null,

  "revision": 0,
  "status": "active"
}
```

### 4.2 Arithmetic Check: Principal Tolerance

The system checks:
```
computed_principal = Decimal(Valor_compra) × Decimal(Acciones)
delta = ABS(Decimal(Total_€) - computed_principal)
```

The tolerance is adaptive:

| Quantity decimal places | Price decimal places | Tolerance |
|------------------------|---------------------|-----------|
| 0 (integer) | ≤ 4 | €0.02 |
| 1–2 | ≤ 4 | €0.05 |
| 3–4 | any | €0.10 |
| ≥ 5 (fractional/repeating) | any | €0.50 |

If `delta > tolerance`:
```
→ WARNING_PRINCIPAL_MISMATCH
  store import_provenance.principal_delta = delta
  store import_provenance.computed_principal = computed_principal
```

The row is still imported. Both `Total (€)` (authoritative as the stated EUR amount) and `Valor compra × Acciones` (the computed principal) are preserved as separate source facts. The user reviews and confirms.

**`Total (€)` is the more trusted value for EUR reporting** because it carries the explicit `(€)` label. When a discrepancy exists, `gross_eur = Total (€)` is what enters the ledger; the computed principal is informational.

### 4.3 Foreign Securities: `Valor compra` in Non-EUR Currency

**Observed pattern (synthetic example):**
```
Empresa: Apple Inc.  |  Valor compra: 182.50  |  Acciones: 50  |  Total(€): 8,391.50  |  Comisión: 9.95
```
Here `182.50 × 50 = 9,125.00` but `Total (€) = 8,391.50`. The arithmetic fails with a large discrepancy — far outside any rounding tolerance. This is a signal that `Valor compra` is in a foreign currency (USD in this example) while `Total (€)` applies the broker's FX conversion.

**Design rule: do not invent FX. Detect, flag, and await user input.**

**Detection:**  
If `delta > €1.00` (a threshold that excludes rounding but captures FX-scale discrepancy):
```
→ WARNING_POSSIBLE_FX_MISMATCH
  import_provenance.suspected_price_currency = null  (cannot determine without user input)
  import_provenance.computed_principal_eur_assumed = computed_principal
```

**Storage before resolution:**
- `price_txn = Valor compra` (stored as-is)
- `price_currency = batch.price_currency` (default `EUR`; the flag indicates this may be wrong)
- `gross_txn = Valor compra × Acciones` (computed, stored as `gross_txn`)
- `gross_eur = Total (€)` (from file, always authoritative EUR)
- `fx.rate = null`, `fx.rate_source = DEFERRED`

These represent two independent facts that are currently inconsistent. The user resolves during reconciliation.

**Resolution options:**
1. **Provide original currency and FX rate explicitly:** User supplies `price_currency = USD` and `fx.rate = 0.91850`. System stores `gross_txn` in USD, `gross_eur` in EUR, and the rate. `fx.rate_source = MANUAL` or `BROKER`.
2. **Derive FX rate from the file's arithmetic:** User confirms `price_currency = USD` and no explicit rate. System computes `fx.rate = Total(€) / (Valor_compra × Acciones)` = implied rate. Stores with `fx.rate_source = DERIVED_FROM_TOTALS`. This is factually correct (it is the exact rate the broker applied) and is labeled clearly as derived — not an ECB rate.
3. **Confirm EUR price:** User confirms `Valor compra` was already in EUR (rare for a foreign security but possible if the broker shows EUR-converted price). Arithmetic discrepancy is then unexplained — store `WARNING_PRINCIPAL_MISMATCH` as a genuine arithmetic issue.

`DERIVED_FROM_TOTALS` is a new `fx.rate_source` enum value for this case. Its meaning: "Rate was computed as `gross_eur / gross_txn` from the source file's own columns. Not an external rate source."

---

## 5. Zero-Cost Rows — `PENDING_SCRIP_CLASSIFICATION`

### 5.1 Definition

A zero-cost row is one where all three of the following are zero (or blank):
```
Valor compra == 0  AND  Total(€) == 0  AND  Comisión == 0
```
while `Acciones > 0`.

These rows **must not** be imported as ordinary BUY movements. A BUY at zero cost would:
- Create a cost basis entry of €0.00 that distorts average cost calculations
- Silently assert that the shares were free (a tax policy statement, not a neutral fact)
- Conflate a pending tax determination with a resolved one

Instead, zero-cost rows are imported as **corporate action candidates** using the `ca_event`/`ca_leg` model.

### 5.2 What Is Created

**One `ca_event`** (incomplete, candidate state):
```jsonc
{
  "id": "cae_{account_id}_{YYYYMMDD}_{empresa_norm_hash}_{seq:05d}",
  "account_id": "_unassigned",  // or real account if specified
  "doc_type": "ca_event",
  "ca_type": "SCRIP_DIVIDEND",     // tentative; may be reclassified
  "ca_type_is_provisional": true,  // explicit flag: user has not confirmed type

  "security": { /* from alias map, or null */ },

  "trade_date": "2024-03-28",     // from Fecha compra (purchase sheet date = acquisition date)
  "payment_date": null,           // unknown — may be linked from dividend import
  "ex_dividend_date": null,       // unknown

  "election": null,               // unknown until classified
  "leg_ids": ["cal_...SHARE_ACQ_..."],

  "reconciliation_status": "PENDING_SCRIP_CLASSIFICATION",
  // Administrative: "no dividend event linked yet; ca_type provisional"

  "assignment_status": "UNASSIGNED",
  "assigned_account_id": null,

  "import_provenance": { /* see §9 */ }
}
```

**One `ca_leg`** of type `SHARE_ACQUISITION`:
```jsonc
{
  "id": "cal_{account_id}_{YYYYMMDD}_{empresa_norm_hash}_SHARE_ACQ_{seq:05d}",
  "account_id": "_unassigned",  // must match parent ca_event
  "doc_type": "ca_leg",
  "leg_type": "SHARE_ACQUISITION",
  "cash_flow_direction": "NONE",

  "trade_date": "2024-03-28",
  "payment_date": null,

  "quantity": "9.0000000000",     // 10 dp — from Acciones column
  "quantity_unit": "shares",

  "fmv_per_share_txn": null,      // unknown; not in the purchase file
  "fmv_txn_currency": null,
  "fmv_reference_date": null,
  "fmv_source": null,

  "cost_basis": {
    "recorded_method": "UNKNOWN",
    // MUST NOT default to ZERO. Zero is a tax policy assertion, not a neutral state.
    // The user must explicitly set this during classification.
    "cost_per_share_eur": null,
    "total_cost_eur": null,
    "basis_note": "Imported from purchase sheet as zero-cost row. Cost basis method requires classification.",
    "basis_set_by": null,
    "basis_set_at": null
  },

  "cash_top_up_leg_id": null,
  "holdings_impact": "INCREASE",   // even pending, shares are real (see §7)

  "reconciliation_status": "PENDING_SCRIP_CLASSIFICATION",
  "linked_ca_event_id": null,   // null until linked to a dividend event

  "tax_status": "n_a",

  "import_provenance": {
    "batch_id": "...",
    "csv_row_number": 12,
    "row_sha256": "...",
    "empresa_raw": "Unilever PLC",
    "empresa_normalized": "unilever",
    "csv_año": 2024,
    "raw_acciones": "9.0000000000"
  }
}
```

### 5.3 What PENDING_SCRIP_CLASSIFICATION Is Not

- It is **not** a warning badge/count in the normal sense. It is a reconciliation-queue item, accessed by year filter (same design as `PENDING_RIGHTS_CLASSIFICATION` in the dividend import).
- It does **not** appear in `import_provenance.import_status[]` (the data-quality codes).
- It is an administrative/enrichment state, not a data-quality problem.
- Records from years prior to 2026 may remain in this state indefinitely without any notification.

### 5.4 Possible Reclassifications (User Decision)

During reconciliation, the user can resolve a `PENDING_SCRIP_CLASSIFICATION` row by selecting one of:

| Classification | Result | Cost basis |
|---------------|--------|-----------|
| Link to existing dividend event as `SHARE_ACQUISITION` leg | Leg migrates to the linked `ca_event`; provisional `ca_event` voided | Inherited from that event's classification (user sets on the matched event) |
| Confirm as standalone scrip acquisition (no cash leg) | Provisional `ca_event` confirmed; user sets cost basis method | User selects: `ZERO`, `FMV_EX_DATE`, `MANUAL_OVERRIDE` |
| Reclassify as ordinary BUY at stated price = 0 | Converted to `ledger_txn` with `txn_type: BUY`, `price_txn: "0.000000"`, cost basis explicitly zero | Explicitly set to zero by user (tax consequence acknowledged) |
| Leave unclassified (indefinitely) | Remains in `PENDING_SCRIP_CLASSIFICATION` state; participates in holdings quantity | No cost basis; holdings page shows "cost basis pending" |

**None of these reclassifications are automatic.** Every path requires an explicit user action.

---

## 6. Safe Linking: Zero-Cost Purchase Rows ↔ Dividend Events

### 6.1 The Linking Need

The user's purchase sheet and dividend sheet are separate files. A row on the purchase sheet (`9 shares, date 2024-03-28, price=0, Unilever`) may correspond directly to a `RIGHTS_OR_SHARE_PENDING` leg on the dividend sheet (`Importe en Derechos = X, Unilever, 2024-03-28`). After both are imported, the user should be able to link them into a single coherent `ca_event`.

### 6.2 Suggestion Algorithm

The system surfaces **candidate matches** — it never creates a link automatically.

**Matching criteria (all applied, scored):**

| Criterion | Match condition | Weight |
|-----------|----------------|--------|
| Security | Same ISIN (if both resolved) OR same normalized `empresa_normalized` (if ISIN unresolved) | Strong |
| Date window | `purchase.trade_date` within 60 days of `dividend.payment_date` OR `dividend.ex_dividend_date` | Strong |
| Year | Same `Año` or same payment year | Medium |
| Quantity plausibility | Purchase `Acciones` could plausibly be the whole-share portion of the dividend entitlement (no formula — purely human judgment) | Weak (informational) |

Candidates are **suggested, not scored for automatic selection**. The UI shows up to 5 candidates for each unclassified zero-cost row, ranked by how many criteria match, with the matching criteria displayed so the user can make an informed choice.

### 6.3 Linking Operation

When the user confirms a link:

1. The zero-cost `ca_leg` (SHARE_ACQUISITION) is **moved** into the target `ca_event` from the dividend import. "Moved" means:
   - The leg's `ca_event_id` is updated to the dividend `ca_event.id`.
   - The provisional `ca_event` created by the purchase import is voided (with `void_reason: "Share acquisition leg moved to linked dividend event {target_ca_event_id}"`).
   - The dividend `ca_event.leg_ids` array gains the share acquisition leg id.
   - Both operations in a Cosmos transactional batch (same `account_id` partition). **This requires both the zero-cost row and the dividend event to be in the same partition.** If they are in different partitions (e.g., zero-cost row is in `_unassigned` but dividend event is in `heytrade_main`), the user must first assign the purchase row to the same account as the dividend event, then link.

2. If the dividend event already has an active non-placeholder `SHARE_ACQUISITION` leg:
   - The system warns: "This dividend event already has a share acquisition record. Linking this purchase row would add a second share acquisition leg. Are you sure?"
   - The user must explicitly confirm to allow a second leg (one-to-many is supported but requires acknowledgment).

3. If the dividend event has a `RIGHTS_OR_SHARE_PENDING` placeholder leg:
   - The placeholder is voided in the same batch.
   - The purchase-derived `SHARE_ACQUISITION` leg (with its known quantity) replaces it.
   - `ca_event.import_reconciliation` is updated with a cross-import note.

### 6.4 One-to-Many Linking

Supported patterns:
- **One zero-cost row → one dividend event:** most common.
- **One zero-cost row → multiple dividend events:** unusual. The system allows it but requires the user to split the quantity across events explicitly. The `SHARE_ACQUISITION` leg is duplicated with user-specified sub-quantities; both link to their respective events. Total quantity across legs must equal the original `Acciones` value.
- **Multiple zero-cost rows → one dividend event:** e.g., shares from one corporate action delivered in two tranches (different dates). Both legs are linked; total quantity is the sum.

All linking creates an `import_provenance.cross_import_link` field on both sides with the other document's id and batch id.

---

## 7. Paid Rows Near Zero-Cost Rows — Classification Suggestion

### 7.1 The Pattern (Synthetic Example)

```
Date       | Empresa   | Qty  | Price  | Total(€) | Fee
2024-03-28 | Unilever  | 9.0  | 0.00   | 0.00     | 0.00   ← zero-cost (scrip)
2024-03-28 | Unilever  | 0.2  | 24.75  | 4.95     | 0.00   ← paid; top-up candidate
```

The second row could be:
a. An ordinary BUY of 0.2 shares at market price, coincidentally same day.
b. A `CASH_TOP_UP` payment to complete a whole-share scrip acquisition.

Both are valid. The system cannot determine which interpretation is correct from the data alone.

### 7.2 Classification Suggestion Rules

The system produces an informational suggestion (not a warning, not a required action) when **all** of the following are true:
- A paid purchase row exists for the same normalized security
- Within `N = 7` days of a zero-cost purchase row for the same security (configurable; default 7 days)
- The paid row's `Acciones` is fractional (< 1.0 whole share), consistent with a top-up for a partial entitlement

Suggestion text (displayed in reconciliation UI only, never as a badge):
> "This paid purchase (0.2 shares, €4.95) is close in date to a zero-cost acquisition (9 shares). It may be a cash top-up to complete a whole-share scrip acquisition. Would you like to classify it as a CASH_TOP_UP linked to that event? [Link as Top-Up] [Keep as BUY]"

### 7.3 If User Confirms as CASH_TOP_UP

1. The paid `ledger_txn` (BUY) is **voided** (soft-delete with reason: "Reclassified as CASH_TOP_UP for scrip event {ca_event_id}").
2. A new `ca_leg` of type `CASH_TOP_UP` is created and linked to the relevant `ca_event`.
3. The `CASH_TOP_UP` leg carries `withholding: null` always (as per the `ca_leg` model).
4. `SHARE_ACQUISITION.cash_top_up_leg_id` is updated to reference the new leg.
5. **Critically: the cost basis of the `SHARE_ACQUISITION` leg is NOT automatically changed.** The `cost_basis.recorded_method` must be re-evaluated by the user after linking the top-up. The top-up amount is now available as a documented fact for the user's tax advisor to apply.

### 7.4 If User Keeps as Ordinary BUY

The paid row remains as a `ledger_txn` with `txn_type: BUY`. The suggestion is dismissed. The classification suggestion is stored in `import_provenance.classification_suggestion` with `outcome: DECLINED` so it does not re-surface.

### 7.5 Suggestion Does Not Change Default Treatment

**All paid rows default to `txn_type: BUY`.** The suggestion is an optional enrichment path, not a required reconciliation step. Paid rows near zero-cost rows participate normally in holdings and cost basis calculations from the moment they are imported, regardless of whether the top-up classification is ever applied.

---

## 8. Row Routing Logic

```
IF Valor compra == 0 AND Total(€) == 0 AND Comisión == 0 AND Acciones > 0
    → ZERO_COST_ROW
    → create ca_event (provisional) + SHARE_ACQUISITION leg
    → reconciliation_status: PENDING_SCRIP_CLASSIFICATION

IF Valor compra == 0 AND Total(€) == 0 AND Comisión == 0 AND Acciones == 0
    → SKIPPED_ALL_ZERO

IF Valor compra > 0 OR Total(€) > 0 (with Acciones > 0)
    → PAID_BUY_ROW
    → create ledger_txn (txn_type: BUY)
    → check arithmetic (§4.2); check FX mismatch (§4.3)

IF Valor compra == 0 AND Total(€) == 0 AND Comisión > 0 (fee only, no principal)
    → ERROR_FEE_WITHOUT_PRINCIPAL
    → row not written (unusual; fee with no trade)

IF Acciones == 0 AND (Total > 0 OR Comisión > 0)
    → ERROR_AMOUNT_WITHOUT_QUANTITY
    → row not written
```

---

## 9. Validation Checks Specific to Purchase Import

The full set of validation checks from the dividend import (§7 of `livingston-dividend-csv-import.md`) applies. Additional checks:

### 9.1 Year vs. Date Cross-Check
Same as dividend import: `WARNING_YEAR_DATE_MISMATCH` if `Año` ≠ `year(Fecha compra)`.

### 9.2 Principal Arithmetic Check
See §4.2: `WARNING_PRINCIPAL_MISMATCH` with adaptive tolerance.

### 9.3 FX Mismatch Detection
See §4.3: `WARNING_POSSIBLE_FX_MISMATCH` when delta > €1.00.

### 9.4 Negative Values
`ERROR_NEGATIVE_AMOUNT` if any of `{Valor compra, Acciones, Total(€), Comisión}` < 0.

### 9.5 Missing Date
`ERROR_MISSING_DATE` or `ERROR_INVALID_DATE` if `Fecha compra` is blank or unparseable.

### 9.6 No Unique Warning Code for Unassigned Account
No `WARNING_ACCOUNT_UNASSIGNED`. Consistent with the dividend import design. Unassigned account is purely administrative.

### 9.7 Status and Warning Code Reference

**Data-quality codes (in `import_provenance.import_status[]`):**

| Code | Level | Written | Action |
|------|-------|---------|--------|
| `IMPORTED_CLEAN` | Info | Yes | None |
| `WARNING_SECURITY_UNRESOLVED` | Warning | Yes | Map company name |
| `WARNING_PRINCIPAL_MISMATCH` | Warning | Yes | Review source data |
| `WARNING_POSSIBLE_FX_MISMATCH` | Warning | Yes | Provide price currency / FX rate |
| `WARNING_YEAR_DATE_MISMATCH` | Warning | Yes | Confirm date |
| `WARNING_POSSIBLE_DUPLICATE` | Warning | Yes | Confirm or void |
| `WARNING_AMBIGUOUS_NUMBER` | Warning | Yes | Confirm parsed value |
| `SKIPPED_ALL_ZERO` | Info | No | None |
| `SKIPPED_DUPLICATE` | Info | No | None |
| `ERROR_NEGATIVE_AMOUNT` | Error | No | Fix source |
| `ERROR_INVALID_DATE` | Error | No | Fix source |
| `ERROR_MISSING_DATE` | Error | No | Fix source |
| `ERROR_INVALID_NUMBER` | Error | No | Fix source |
| `ERROR_FEE_WITHOUT_PRINCIPAL` | Error | No | Fix source |
| `ERROR_AMOUNT_WITHOUT_QUANTITY` | Error | No | Fix source |

**Reconciliation-queue state (administrative, NOT in import_status, NOT in badge counts):**

| State | Where stored | Meaning |
|-------|-------------|---------|
| `PENDING_SCRIP_CLASSIFICATION` | `ca_event.reconciliation_status` and `ca_leg.reconciliation_status` | Zero-cost row awaiting user classification |
| Classification suggestion | `import_provenance.classification_suggestion` | Informational top-up candidate; not a required action |

---

## 10. Deduplication and Idempotency

### 10.1 Row Hash (Level 1 — Exact Re-Import)

Identical to dividend import: SHA-256 of the normalized raw row string. Checked against both the target partition and the `_unassigned` partition (cross-partition query on `row_sha256`).

### 10.2 Semantic Dedup Key (Level 2 — Near-Duplicate)

```
For paid BUY:    {account_id}|{empresa_normalized}|{trade_date}|{quantity_6dp}|{total_2dp}
For zero-cost:   {account_id}|{empresa_normalized}|{trade_date}|{quantity_6dp}|ZERO_COST
```

**Key design decision: quantity is part of the semantic key.**

This handles the observed legitimate pattern of multiple same-company same-day purchases with different quantities:

```
Synthetic example:
2024-01-15 | Iberdrola | 100 shares | €12.34 | €1,234.00 | €9.95   ← legitimate first buy
2024-01-15 | Iberdrola | 200 shares | €12.36 | €2,472.00 | €9.95   ← legitimate second buy (different tranche)
```

Both rows have different quantities → different semantic keys → both imported cleanly. No false positive duplicate warning.

Two truly identical rows (same company, same date, same quantity, same total) → same semantic key → `WARNING_POSSIBLE_DUPLICATE`. The user decides whether it is a legitimate duplicate purchase or a file artifact.

### 10.3 Cross-Import Duplicate: Same Share Acquisition in Both Files

A share acquisition may appear in both the dividend sheet (as `Importe en Derechos`) and the purchase sheet (as a zero-cost row). After both are imported, linking the rows (§6.3) resolves the redundancy. Until linked, both records coexist — but neither creates a double-count problem because:

- The `RIGHTS_OR_SHARE_PENDING` placeholder leg from the dividend import is excluded from holdings calculations.
- The zero-cost `SHARE_ACQUISITION` leg from the purchase import IS counted in holdings (§11).

After linking: the zero-cost purchase leg takes over; the placeholder is voided. Holdings count remains unchanged (one leg, one count).

---

## 11. Holdings Impact

### 11.1 Paid BUY Rows

Participate in holdings and average cost basis **immediately** upon import:
```
holding_qty += quantity          (at trade_date)
avg_cost_basis_eur += (gross_eur + fees_eur) / quantity   (weighted)
```

If `WARNING_POSSIBLE_FX_MISMATCH` is unresolved: the `gross_eur = Total(€)` is used as-is (it is always EUR from the file). Holdings and EUR cost basis are valid even with unresolved `price_currency`. Only the `price_txn`/`txn_currency` interpretation is uncertain — not the EUR total.

### 11.2 Zero-Cost Pending Rows — Holdings Inclusion Recommendation

**Design recommendation: include zero-cost acquisitions in holdings quantity; exclude from cost basis averages until classified.**

Rationale:
- The shares are physically in the investor's portfolio. Excluding them gives an incorrect holding count (broken covered-call logic, incorrect Securities page total).
- The cost basis is genuinely unknown — including it at €0 would silently assert zero cost, which is a tax interpretation, not a neutral fact.
- Excluding from average cost while including in quantity is the correct separation: the system knows *how many shares* but not *at what cost*.

**Implementation:**

The holdings derivation formula (from `decisions.md`) is extended:

```
holding_qty =
    SUM(BUY.quantity where txn_type=BUY)
  - SUM(SELL.quantity where txn_type=SELL)
  + SUM(SHARE_ACQUISITION.quantity where reconciliation_status != PENDING_SCRIP_CLASSIFICATION
        OR include_pending_in_qty = true)   ← always true by this design
```

```
avg_cost_basis_eur =
  WEIGHTED_AVG(gross_eur + fees_eur, BUY.quantity)
  + WEIGHTED_AVG(cost_basis.cost_per_share_eur × quantity, SHARE_ACQUISITION
                 where recorded_method != UNKNOWN)
  -- pending (UNKNOWN method) SHARE_ACQUISITION legs excluded from denominator
```

**Holdings page display for affected securities:**

| Field | Value when pending zero-cost legs exist |
|-------|----------------------------------------|
| Quantity | Correct full count including pending acquisitions |
| Avg cost/share | Based on confirmed BUY records and classified acquisitions only |
| Cost basis status | `INCOMPLETE` indicator (informational; not a warning badge) |
| Label | "Cost basis excludes N unclassified share acquisitions" |

This ensures the securities page shows accurate share counts (critical for covered-call eligibility calculation against `total_shares`) while clearly signaling that average cost is not the full picture.

---

## 12. Provenance — Purchase Import Specifics

The `import_provenance` subobject follows the same structure as the dividend import (§9 of `livingston-dividend-csv-import.md`) with the following additions:

```jsonc
"import_provenance": {
  // ... all standard fields from dividend import ...

  "raw_valor_compra": "182.5000000000",   // raw parsed price; source fact
  "raw_acciones": "9.0000000000",          // raw parsed quantity; source fact

  "computed_principal": "27375.000000",    // Valor × Acciones (computed, not from file)
  "principal_delta": null,                 // populated if WARNING_PRINCIPAL_MISMATCH

  "classification_suggestion": {
    "type": null,                          // "CASH_TOP_UP_CANDIDATE" if suggestion triggered
    "candidate_ca_event_id": null,
    "outcome": null                        // null | ACCEPTED | DECLINED
  },

  "cross_import_link": null
  // Populated when this record is linked to a document from another batch:
  // { "linked_doc_id": "...", "linked_batch_id": "...", "link_type": "SHARE_ACQ_TO_DIVIDEND" }
}
```

Shared fields (`row_sha256`, `csv_row_number`, `empresa_raw`, `empresa_normalized`, `csv_año`, `batch_id`, `import_status`, `raw_row`) are identical to the dividend import.

---

## 13. Company Alias Map — Shared Across Both Importers

The `security_alias_map` document (§6 of `livingston-dividend-csv-import.md`) is the single shared alias registry for both the dividend and purchase importers. No separate map for purchases. Resolution via the dividend importer's alias map UI is available to both. Adding an alias while resolving a purchase security updates the same map that the dividend importer consults.

The `_global` partition value that hosts the alias map (§3.4.2 of the dividend contract) serves both importers.

---

## 14. Phase Placement and API Additions

### 14.1 Phase Assignment

The purchase CSV import is **Phase 1b alongside the dividend CSV import**. They share:
- The same `portfolio_ledger` container and Cosmos provisioning
- The same `_unassigned` partition infrastructure
- The same alias map
- The same dedup framework
- The same account-optional policy

The purchase import does not depend on the dividend import being complete first. Both can be imported in either order. Cross-import linking (§6) is available after both are partially or fully imported.

### 14.2 API Additions Required

New endpoints (in addition to the existing dividend import endpoints):

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/portfolio/purchases/import` | Accept upload of purchase CSV/Excel, batch metadata, return batch id and summary |
| `GET` | `/api/portfolio/purchases/reconciliation?year=2026` | Year-filtered queue of `PENDING_SCRIP_CLASSIFICATION` events |
| `POST` | `/api/portfolio/ca-events/{id}/classify-scrip` | Confirm/reclassify a pending zero-cost acquisition |
| `POST` | `/api/portfolio/ca-events/{id}/link-purchase-row` | Link a zero-cost purchase row to an existing dividend ca_event |
| `POST` | `/api/portfolio/ledger-txn/{id}/classify-top-up` | Reclassify a paid BUY as CASH_TOP_UP for a ca_event |
| `GET` | `/api/portfolio/purchases/link-candidates?ca_event_id={id}` | Suggest dividend events matching a zero-cost purchase row |
| `GET` | `/api/portfolio/ca-events/{id}/link-candidates?source=purchase` | Suggest zero-cost purchase rows matching a dividend event (reverse direction) |

The `/api/portfolio/accounts/assign` endpoint from the dividend import is reused without change for account assignment of purchase records.

### 14.3 Reconciliation Lifecycle for Zero-Cost Rows

```
IMPORT
  └─ zero-cost row arrives
  └─ ca_event + SHARE_ACQUISITION leg created (PENDING_SCRIP_CLASSIFICATION)
  └─ participates in holdings quantity; excluded from cost basis

RECONCILIATION (year-filtered queue, default 2026)
  ├─ Option A: Link to dividend event
  │    └─ leg moves to dividend ca_event; provisional ca_event voided
  │    └─ cost basis set on confirmed SHARE_ACQUISITION leg
  │    └─ reconciliation_status: CLASSIFIED_AND_LINKED
  │
  ├─ Option B: Confirm as standalone scrip (no dividend link)
  │    └─ provisional ca_event confirmed; user sets cost basis method
  │    └─ reconciliation_status: CLASSIFIED_STANDALONE
  │
  ├─ Option C: Reclassify as explicit zero-cost BUY
  │    └─ ca_event voided; new ledger_txn created (BUY, price=0, cost_basis=0)
  │    └─ user acknowledges tax implication
  │    └─ participates in cost basis averages at €0
  │
  └─ Option D: Leave unclassified
       └─ reconciliation_status: PENDING_SCRIP_CLASSIFICATION (permanently valid)
       └─ participates in holdings quantity; excluded from cost basis
       └─ no notification; no badge; accessible via reconciliation filter
```

---

## 15. Open Questions

| # | Question | Impact |
|---|----------|--------|
| Q1 | Does the purchase file contain SELL rows (negative quantities or a separate SELL indicator)? | If yes, the row router must handle sells; otherwise the first negative-quantity row would be an `ERROR_NEGATIVE_AMOUNT`. |
| Q2 | Are there purchase rows from multiple brokers in the same file (like the open question in the dividend contract)? | If mixed, same solution applies: import unassigned, assign per-broker later. |
| Q3 | Can a zero-cost row have a non-zero `Comisión` (fee for share allocation service)? | Currently routed as `ERROR_FEE_WITHOUT_PRINCIPAL`. If this pattern exists, add a `ZERO_PRICE_WITH_FEE` variant that creates a ca_event with a small `CASH_TOP_UP` leg for the fee. |
| Q4 | For the FX mismatch case (§4.3), should the system pre-populate a suggested implied FX rate from `Total(€) / (Valor × Acciones)` in the reconciliation UI? | Useful UX shortcut; the formula is clearly documented as `DERIVED_FROM_TOTALS`. Low risk if labeled. |

---

## 16. Summary

**7-column format:** `Año`, `Empresa`, `Fecha compra`, `Valor compra`, `Acciones`, `Total (€)`, `Comisión`. Spanish locale (comma decimal, DD/MM/YYYY). File format detection reused from dividend import.

**`Total (€)` = principal only** (`Valor compra × Acciones`), not including commission. Total cash outflow = `Total(€) + Comisión`. Both are stored as independent source facts. Neither overwrites the other; discrepancies produce `WARNING_PRINCIPAL_MISMATCH`.

**`Valor compra` currency:** Stored as source-provided EUR by default. If arithmetic suggests a hidden FX conversion (delta > €1.00), `WARNING_POSSIBLE_FX_MISMATCH` is raised. User provides the original currency and the system stores `fx.rate_source: DERIVED_FROM_TOTALS` (implicit rate from the file's own arithmetic) or a user-supplied explicit rate. `Total (€)` is always the authoritative EUR amount.

**Precision:** Quantities at 10 decimal places; unit prices at 10 decimal places. All stored as decimal strings; no IEEE 754 floats anywhere.

**Zero-cost rows (`price=0, total=0, fee=0, shares>0`):** Imported as a `ca_event` (provisional `SCRIP_DIVIDEND`) + `SHARE_ACQUISITION` leg with `cost_basis.recorded_method: UNKNOWN`. Not an ordinary BUY. Not automatically assigned zero cost basis. Enter a year-filtered reconciliation queue (default 2026 for this migration); prior-year records remain unreconciled without warning.

**Safe linking:** The system suggests — never auto-links — zero-cost purchase rows to matching dividend events. Linking is confirmed by the user; one-to-one, one-to-many, and many-to-one are all supported with full auditability.

**Top-up classification:** Paid rows close to zero-cost rows receive an informational suggestion that they may be `CASH_TOP_UP` legs. Default treatment is ordinary BUY. Reclassification is user-confirmed and does not automatically change the share cost basis.

**Holdings:** Zero-cost pending acquisitions **are** counted in holding quantity (the shares are real). They are **excluded** from average cost basis until classified. The securities page shows the correct quantity with a `cost basis: INCOMPLETE` flag.

**Dedup:** Semantic key includes quantity — enabling legitimate same-day same-company different-quantity purchases without false positives.

**Account optional:** Same `_unassigned` partition design; no warning; assignment at user's pace.

**Phase 1b:** Runs alongside dividend CSV import. Same container, same alias map, same infrastructure. No prerequisites beyond the dividend import's Phase 1b baseline.
