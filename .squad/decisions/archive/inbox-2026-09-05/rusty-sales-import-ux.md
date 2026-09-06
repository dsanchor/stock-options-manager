# Sales History CSV Import — UX & Wizard Design

**Author:** Rusty (Agent Dev — frontend/integration)  
**Date:** 2026-09-05  
**Directive:** `copilot-directive-20260905T170523+0200.md`  
**Depends on:** `rusty-purchase-import-ux.md` (wizard framework, alias mapping, account-optional rules)  
**Depends on:** `rusty-dividend-import-ux.md` (reconciliation queue, year-filter, partial-import mechanics)  
**Aligns with:** `basher-sales-csv-validation.md` (semantic contract, tolerance rules, ledger-replay spec)  
**Status:** Design only — no production code. No user financial data reproduced.  
**Phase:** 1c — extends the shared wizard framework alongside Phase 1.5 (dividends) and 1b (purchases).

---

## 1. Source Format: Fixed 6-Column Sales CSV

The import consumes **only the first 6 columns**. Columns beyond position 6 are silently ignored.

| Position | Column name (Spanish) | Normalized field | Type | Notes |
|---|---|---|---|---|
| 1 | `Año` | `year` | integer | Cross-validated against sale date |
| 2 | `Empresa` | `company_raw` | string | Alias mapping shared with dividend and purchase imports |
| 3 | `Fecha venta` | `sale_date_raw` | string → date | DD/MM/YYYY or ISO YYYY-MM-DD; strict parse |
| 4 | `Acciones` | `shares` | high-precision decimal | Fractional shares; minimum 6dp stored; must be > 0 |
| 5 | `Comisión` | `commission_eur` | high-precision decimal | Broker fee; ≥ 0; required (use 0.00 for zero) |
| 6 | `Total Venta` | `gross_proceeds_eur` | high-precision decimal | Gross sale proceeds; authoritative source field; ≥ 0 |

**Derivation contract (never written to source, never stored as source fields):**

| Derived field | Formula | Purpose |
|---|---|---|
| `net_cash_eur` | `gross_proceeds_eur − commission_eur` | Actual cash received; may be negative if commission > proceeds |
| `unit_price_derived` | `gross_proceeds_eur ÷ shares` | Informational display only; never used to recompute proceeds; labeled "Calculated" throughout the UI |

**`unit_price_derived` provenance rule:** this field is computed from `gross_proceeds_eur` and `shares` for display convenience. It is **never persisted** as a source price, never round-tripped back into the ledger, and never used to recompute `gross_proceeds_eur` in any future calculation. Every downstream computation (realized gain, holding cost) uses `gross_proceeds_eur` (gross) and `net_cash_eur` (net) directly from stored source values.

**Currency:** all fields are EUR. No per-row currency column; no FX input needed.

---

## 2. Import Type Selector — Now Three Types

The `/portfolio/import` type selection screen gains a third card:

```
┌──────────────────────────────────────────────────────────────────────┐
│  Import Historical Records                                           │
│                                                                      │
│  ┌────────────────────┐  ┌────────────────────┐  ┌────────────────┐ │
│  │  💰 Dividends      │  │  🛒 Purchases       │  │  📤 Sales      │ │
│  │  8 columns         │  │  7 columns          │  │  6 columns     │ │
│  │  Año · Empresa ·   │  │  Año · Empresa ·    │  │  Año · Empresa │ │
│  │  Fecha de cobro ·  │  │  Fecha compra ·     │  │  Fecha venta · │ │
│  │  Importe Bruto ·   │  │  Valor compra ·     │  │  Acciones ·    │ │
│  │  ...               │  │  Acciones ·         │  │  Comisión ·    │ │
│  │                    │  │  Total (€) ·        │  │  Total Venta   │ │
│  │                    │  │  Comisión           │  │                │ │
│  └────────────────────┘  └────────────────────┘  └────────────────┘ │
│       [ Select ]               [ Select ]             [ Select ]    │
└──────────────────────────────────────────────────────────────────────┘
```

Deep-link: `/portfolio/import?type=sales` skips the selector.

---

## 3. Parse Strategy

### 3.1 Reused from Existing Wizard

All three import types share the same parser core:
- Delimiter auto-detection: tab → semicolon → comma (with comma-decimal ambiguity guard)
- Spanish number parsing: comma-decimal, period-thousands
- XLSX: SheetJS client-side, sheet 0 only
- High-precision decimal transport: raw cleaned decimal strings (not JS `parseFloat`)

### 3.2 Date Parsing for Sales

`Fecha venta` accepts two formats (stricter than dividend/purchase parsers which accept only DD/MM/YYYY):
1. `DD/MM/YYYY` — primary Spanish format
2. `YYYY-MM-DD` — ISO accepted (common in Excel exports)

Ambiguous 2-digit year formats (e.g. `15-Mar-24`) produce a `date_ambiguous` warning (non-blocking) rather than a block, because some broker exports use this format. The parser resolves the 2-digit year as 2000+ if ≤ current year, 1900+ otherwise, and flags it for user confirmation.

Future dates (sale_date > today at import time) are **blocking** — a future sale cannot be historical.

### 3.3 Batch Metadata for Sales

```
┌──────────────────────────────────────────────────────────────┐
│  Step 1 of 3 — Batch Metadata (Sales)                        │
│  {N} rows parsed · {W} with warnings                        │
│                                                              │
│  ── Required ──────────────────────────────────────────────  │
│  Source currency *       [EUR (fixed for this format)]       │
│                                                              │
│  ── Optional ──────────────────────────────────────────────  │
│  Broker / Account        [(none) ▾ | existing accounts...]  │
│                          "Can be assigned later — leaving    │
│                          blank does not affect data quality" │
│                                                              │
│  ── Inventory context ─────────────────────────────────────  │
│  ℹ️  Inventory warnings are based on all currently           │
│  committed purchase records. If purchase history has not     │
│  yet been imported, negative-inventory warnings are          │
│  expected and non-blocking. They resolve automatically       │
│  when purchase batches are committed later.                  │
│                                                              │
│  [ ← Back ]                              [ Preview → ]      │
└──────────────────────────────────────────────────────────────┘
```

The inventory-context note is new for sales. It sets the correct expectation: negative-inventory warnings at preview time are not errors to fix now, but they will resolve as purchase history is added.

---

## 4. Row Classification

| Priority | Name | Trigger | Status chip | Maps to |
|---|---|---|---|---|
| 1 | **`SELL`** | `shares > 0` AND `gross_proceeds_eur > 0` | `✅ Ready` or `⚠️ Warning` | `ledger_txn: SELL` |
| 2 | **`ZERO_PROCEED_SALE`** | `shares > 0` AND `gross_proceeds_eur = 0` AND `commission_eur = 0` | `⚠️ Zero proceeds` | `ledger_txn: SELL` with zero-proceeds flag; non-blocking |
| 3 | **`ZERO_QUANTITY`** | `shares = 0` | `🔴 Blocking` | Not imported |
| 4 | **`NEGATIVE_VALUE`** | Any of: `shares < 0`, `gross_proceeds_eur < 0`, `commission_eur < 0` | `🔴 Blocking` | Not imported |
| 5 | **`PARSE_ERROR`** | Any field unparseable | `🔴 Blocking` | Not imported |
| 6 | **`FUTURE_DATE`** | `sale_date > today` | `🔴 Blocking` | Not imported |
| 7 | **`DATE_YEAR_MISMATCH`** | Year column ≠ year in sale_date | `🔴 Blocking` | Not imported |

**No auto-reclassification of small or zero proceeds.** A `ZERO_PROCEED_SALE` row stays classified as a zero-proceed sale — it is never silently converted to a different event type (e.g., a return of capital or a dividend). If the user believes the event is something else, they reclassify post-import via the Movements page reclassify action.

**Commission > gross proceeds** is not a block — it produces `commission_exceeds_proceeds` warning and shows a negative `net_cash_eur`. Economically unusual but legally possible (minimum broker fees on very small lots). The user explicitly acknowledges.

---

## 5. Preview Table Structure

| # | Column | Description |
|---|---|---|
| — | Checkbox | Include/exclude (checked by default; blocking rows auto-unchecked) |
| — | Status chip | `✅ Ready` · `⚠️ Warning` · `⚠️ Zero proceeds` · `🔴 Blocking` |
| 1 | Year | Parsed integer |
| 2 | Security | Resolved link OR ⚠️ "Unknown: {company_raw}" with [Map] button |
| 3 | Sale Date | Normalized display (YYYY-MM-DD) |
| 4 | Shares | High precision; compact 4dp + full-precision tooltip |
| 5 | Gross Sale | `Total Venta` verbatim; full precision |
| 6 | Commission | `Comisión` verbatim |
| 7 | **Net Cash** | `gross_proceeds − commission`; bold; amber if < 0 |
| 8 | **Unit Price** *(Calculated)* | `gross_proceeds ÷ shares`; always labeled **"Calculated — not from source"**; italicised or muted style; compact 4dp |
| — | Inventory | `✅ {N} shares available` · `⚠️ Short {N} shares` · `— (no prior data)` |
| — | Realized Gain | `Pending cost basis` (always at import time — informational) |
| — | Account | "(unassigned)" in muted text — **no warning chip** |
| — | Warnings | Chip list |

### 5.1 Unit Price Labeling

The "Unit Price (Calculated)" column header includes a persistent *(i)* tooltip:

> "This price is derived from Total Venta ÷ Acciones for display only. It is not a source field and is not stored. All accounting computations use Total Venta (gross) and Net Cash directly."

The column cells use a visually distinct style (italic, muted foreground, no bold) to signal that they are derived, not source. On mobile, the column collapses by default; tap-to-expand shows it with the "(Calculated)" label.

### 5.2 Net Cash Display

- Positive `net_cash_eur`: standard formatting.
- Zero `net_cash_eur`: amber text, `⚠️ €0.00` to signal unusual case (commission = proceeds).
- Negative `net_cash_eur`: red text, `⚠️ −€X.XX` — commission exceeds proceeds.

### 5.3 Zero-Proceed Sale Rows

`ZERO_PROCEED_SALE` rows show with an amber-tinted background. Gross Sale, Net Cash, and Unit Price columns all display `€0.00`. The inline-expand shows:

```
⚠️ Zero-proceed sale — classified as ZERO_PROCEED_SALE
  This may represent a sale of worthless securities.
  Realized gain will reflect the full cost basis as a capital loss
  once lot history and cost method are available.
  [Import as zero-proceed sale]  [Exclude row]
```

The user must explicitly leave the row checked (no auto-exclude) before committing. This forces acknowledgement without being a block.

---

## 6. Warning Taxonomy — Sales-Specific

Inherits shared warnings (`parse_error`, `unresolved_security`, `date_year_mismatch`, `all_zero`, `duplicate`) with blocking behavior as per the existing pattern. Adds:

| Code | Severity | Trigger | User message |
|---|---|---|---|
| `negative_inventory` | warn | Reconstructed holdings < 0 at sale date | "Insufficient holdings on record at sale date — {N} shares short. This will resolve automatically if earlier purchases are imported later." |
| `rg_unavailable` | info | Always at import time | Shown as `Pending cost basis` in the Realized Gain column; informational banner in Step 3; not a chip on the row. |
| `commission_exceeds_proceeds` | warn | `commission_eur > gross_proceeds_eur` | "Commission ({C}) exceeds gross proceeds ({TV}). Net cash: {NC} (negative). Confirm this is correct — can occur with minimum fees on small lots." |
| `net_cash_zero` | warn | `net_cash_eur = 0` (commission = proceeds) | "Commission equals gross proceeds — net cash received is zero. Confirm this is intentional." |
| `zero_proceed_sale` | warn | `gross_proceeds_eur = 0` and `commission_eur = 0` | See §5.3. |
| `split_sale_proximity` | warn | Same security, same date, two rows with derived unit prices within 1% of each other | "Two sales of the same security on the same date at nearly identical prices. May be one transaction recorded twice, or two broker fills. Confirm both are intentional." |
| `date_ambiguous` | warn | 2-digit year in date string | "Sale date year was inferred as {YYYY}. Confirm this is correct." |
| `future_date` | **block** | `sale_date > today` | "Sale date is in the future." |
| `date_year_mismatch` | **block** | Year column ≠ sale_date year | "Year column ({Año}) contradicts sale date year ({date_year})." |
| `zero_quantity` | **block** | `shares = 0` | "Zero quantity is meaningless for a sale." |
| `negative_quantity` | **block** | `shares < 0` | "Negative share quantity." |
| `negative_total` | **block** | `gross_proceeds_eur < 0` | "Negative gross proceeds." |
| `negative_commission` | **block** | `commission_eur < 0` | "Negative commission." |

**Administrative vs data-quality separation:** same rule as all other import types. Unassigned account is not a warning code and does not affect row status.

**`negative_inventory` is always non-blocking.** The ledger is built incrementally; a sale imported before its corresponding purchases will naturally show negative inventory. The warning is informational and persists until purchase history covers it, at which point it auto-resolves via ledger replay (see §9).

---

## 7. Inventory Check at Preview Time

For each resolved-security SELL row, the system calls `GET /api/portfolio/positions/inventory?security_id=&at_date=` to compute the reconstructed holding at the sale date using only currently **committed** records:

```
inventory(security, date) =
    SUM(SHARE_ACQUISITION quantity, dates ≤ date, committed)
  + SUM(BUY quantity, dates ≤ date, committed)
  − SUM(SELL quantity, dates < date, committed)
```

**Intra-batch inventory:** rows within the current batch are evaluated in ascending date order. An earlier sale in the same batch reduces inventory available to a later sale in the same batch — even before any are committed.

**Display in preview:**
- `✅ {N} shares available` — inventory ≥ sale quantity
- `⚠️ Short {N} shares` — inventory < sale quantity (amber badge)
- `— (no prior data)` — security has no committed records (expected if purchase history not yet imported)

The inventory column is **read-only and recalculated server-side** at each preview-load. It is not cached client-side across sessions.

**Split-sale intra-batch inventory:** when two rows sell the same security on the same date, both are included in the inventory calculation. The combined quantity of both rows is checked against available inventory; if the combined total would cause shortfall, both rows receive the `negative_inventory` warning independently, with a cross-reference note: "Combined with another same-date sale row — see row {index}."

---

## 8. Realized Gain: `Pending Cost Basis`

### 8.1 Display in Preview and Movements

The Realized Gain column in the preview table shows `Pending cost basis` for every row. This is the permanent initial state of any committed sale record. It is informational, not an error.

In the Movements table, the same `Pending cost basis` badge (muted, non-alarming style) appears in a "Realized Gain" column until the conditions for computation are met.

### 8.2 Conditions for RG Availability

Realized gain `RG = gross_proceeds − commission − cost_basis` becomes computable only when:

1. The security has sufficient committed purchase lots (and/or SHARE_ACQUISITION legs) covering the sale quantity on or before the sale date, **AND**
2. A **cost method** has been declared for the security or portfolio level

Until both conditions hold, the Realized Gain column shows `Pending cost basis` regardless of how many purchase records exist.

**Partial coverage:** if committed lots cover < 100% of the sale quantity, the gain is computed for the covered portion and marked `Partial — {covered}%`. The uncovered portion remains `Pending cost basis`. Remaining partial state clears when additional purchase records cover the gap.

**`RG` is never shown in the import wizard itself.** It is a post-import computed value visible only in the Movements detail page and reconciliation report.

### 8.3 Cost Method Declaration

A security's cost method (FIFO / LIFO / Average Cost / Specific Lot) is configured outside the import wizard, on the `/portfolio/securities/{ticker}` detail page. A prominent prompt appears there when a security has committed sale records but no declared method:

```
┌─────────────────────────────────────────────────────────────────┐
│ ⚠️  Realized gains pending — declare cost method                │
│  {N} sale records for {TICKER} are awaiting a cost method       │
│  declaration before realized gains can be computed.              │
│  Cost method: [FIFO ▾ | LIFO | Average Cost | Specific Lot]    │
│  [ Save method — compute gains for all {N} sales → ]            │
└─────────────────────────────────────────────────────────────────┘
```

Declaring a cost method triggers immediate RG computation for all eligible sales of that security. If the method is later changed, all RG values are recomputed (method change is not retroactively applied partially — it replaces all previous computations for the security).

---

## 9. Ledger Replay and Auto-Resolution of Warnings

### 9.1 What Triggers a Replay

After **any** import batch is committed — purchases, sales, dividends, or corporate actions — the system triggers a **ledger replay** for every security touched by the committed batch:

1. All committed records for the security are sorted chronologically.
2. Inventory is recalculated at each transaction date.
3. `negative_inventory` warnings on existing committed sale records are re-evaluated.
4. Warnings that are now resolved (inventory ≥ 0 after replay) are cleared.
5. Warnings that remain (still insufficient prior purchases) stay open.
6. Newly computable RG values (new lots now cover a previously uncovered sale) are computed.

### 9.2 Post-Import Replay Report

The post-commit completion banner (visible after any batch is committed) includes a replay summary:

```
┌──────────────────────────────────────────────────────────────────┐
│ ✅ Import complete — {N} sales recorded.                         │
│ Account: (unassigned — assign after import)                      │
│                                                                  │
│ 🔄 Ledger replay: {M} securities replayed.                       │
│                                                                  │
│   {TICKER_A} ——  2 inventory warnings cleared.                  │
│                   RG now available for 3 sales (2021–2022).     │
│                                                                  │
│   {TICKER_B} ——  1 inventory warning remains                    │
│                   (15 shares still missing before 2023-06-01).  │
│                   Import earlier purchase records to resolve.   │
│                                                                  │
│   {TICKER_C} ——  No change.                                     │
│                                                                  │
│ [ View reconciliation queue → ]  [ Assign account → ]           │
└──────────────────────────────────────────────────────────────────┘
```

This same replay report is generated when a **purchase batch** is committed — existing sale records benefit retroactively without any user action.

### 9.3 Replay Does Not Modify Source Data

Ledger replay only updates the **computed state** of records (warning flags, RG values). It never modifies source fields (`gross_proceeds_eur`, `commission_eur`, `shares`, `sale_date`). The immutable source fact is preserved; only the derived interpretation changes.

---

## 10. Reconciliation Queue — Sales

### 10.1 Queue Integration

Sales produce three categories of pending items in the existing reconciliation queue at `/portfolio/movements?status=pending_reconciliation&year=2026`:

| Status | Icon | Source | Description |
|---|---|---|---|
| `negative_inventory` | 🟠 Oversold | Sale records | Holdings < 0 at sale date; awaiting purchase import |
| `pending_cost_basis` | 🟡 Pending gain | Sale records | Committed sale, no RG yet (no cost method or insufficient lots) |
| `no_cost_method` | 🟡 No method | Security level | Security has sales but no declared cost method |

### 10.2 Extended Type Filter

The queue's type filter is now:

```
Type: [All ▾ | Rights/Div | Corp. action | Negative inventory | Pending gain | Cost basis]
```

| Filter value | Shows |
|---|---|
| Rights/Div | `pending_legs`, `pending_cost_basis` from dividend imports |
| Corp. action | `pending_event_link`, `pending_legs` from purchase imports |
| Negative inventory | `negative_inventory` sale records (any year) |
| Pending gain | Sale records where RG = `UNAVAILABLE` or `PARTIAL` |
| Cost basis | Securities with no declared cost method |

### 10.3 Year Filter Behavior for Sales

**Default year = 2026** (same as other types). However, `negative_inventory` and `pending_cost_basis` warnings on pre-2026 records are:
- Stored permanently on the record
- Surfaced in the year they occurred when that year is selected in the filter
- **Not shown in the 2026 default view** unless the sale date is in 2026
- Automatically cleared by ledger replay regardless of year (replay is year-agnostic)

The queue header always shows a muted note when pre-2026 pending items exist:

```
{N} older sales (pre-2026) have unresolved inventory or gain warnings.
Select "All years" or a specific year to review them.
```

### 10.4 Negative Inventory Reconciliation

The queue table for `negative_inventory` rows shows:

```
Date       | Ticker | Shares Sold | Available at Date | Shortfall | Status
2023-06-01 | {TKR}  | 80          | 50                | −30       | 🟠 Oversold
```

**Action: [Details →]** opens a slide-over showing:
- All committed purchases for the security on or before the sale date
- The combined total vs the sale quantity
- "Import earlier purchases to resolve — or void this sale if it was entered incorrectly"
- No edit action on financial fields (append-only ledger); only void is offered

### 10.5 Pending Gain Reconciliation

The queue entry for `pending_cost_basis` rows links directly to the security's detail page to declare a cost method. No separate form is needed — cost method declaration is the action.

---

## 11. Duplicate Detection for Sales

### 11.1 Dedup Key

Account excluded (same principle as all other import types):

```
{security_id}|SELL|{sale_date}|{shares}|{gross_proceeds_eur}|{commission_eur}
```

All six semantic components are included. Unlike purchases (which use only shares + total), sales include commission in the key because two sales of the same lot on the same day with different commission rates are distinct broker transactions.

### 11.2 Intra-Batch Duplicates

If two rows in the same batch produce identical dedup keys, both rows are flagged `🔴 Blocking` in the preview — an exact same-day, same-company, same-quantity, same-total, same-commission duplicate within the same file is treated as a data error and cannot be imported. The user must fix the source or exclude one row.

### 11.3 Cross-Batch Duplicates

A dedup key matching an existing committed record produces a `⚠️ duplicate` warning (non-blocking, consistent with the dividend and purchase import pattern). The user can:
- Leave it checked (imports with a "possible duplicate" flag for post-import review)
- Uncheck it (excluded from this batch)

No auto-skip. The row appears in the Movements table with a `possible duplicate` badge visible in the record detail.

### 11.4 Split-Sale Proximity Check

After dedup resolution, rows are checked for split-sale proximity within the same batch:
- Same `security_id`
- Same `sale_date`
- Derived unit prices within 1% of each other (`|UP_A − UP_B| / max(UP_A, UP_B) < 0.01`)

Both rows receive the `split_sale_proximity` advisory chip. Neither is blocked. The user reviews and confirms both are intentional fills.

---

## 12. Partial Import and Exclusion Controls

Identical to the dividend and purchase import mechanics:

1. Individual row checkbox (checked by default; blocking rows auto-unchecked)
2. "Exclude all Blocking rows" (auto-applied on first preview render)
3. Bulk status chips: "Select all Ready" / "Deselect all Warning" / "Exclude all Zero-proceed"
4. "Acknowledge all Warnings" — a single-click action that marks all non-blocking warning rows as user-confirmed, activating the Import button without requiring per-row acknowledgement

Step 3 always shows exact counts per category before the import executes.

**Sales-specific exclusion helper:**
- **"Show only inventory warnings"** — scrolls preview to the first `negative_inventory` row and applies a status filter
- **"Show only commission > proceeds"** — filters to `commission_exceeds_proceeds` rows for focused review

---

## 13. Step 3 — Review & Import (Sales Summary)

```
┌──────────────────────────────────────────────────────────────┐
│  Step 3 of 3 — Review & Import (Sales)                       │
│                                                              │
│  Account:         (unassigned — assign after import)         │
│  Source currency: EUR (all amounts)                          │
│                                                              │
│  ─── Import summary ────────────────────────────────────────  │
│                                                              │
│  ✅  86  Ready SELL rows — will create ledger_txn: SELL      │
│  ⚠️  12  Warning rows — imported with warnings attached      │
│          of which: 8 negative inventory · 3 commission≥TV    │
│                    1 split-sale proximity                    │
│  ⚠️   2  Zero-proceed sales — imported with flag             │
│  🔴   3  Blocking rows — excluded                            │
│  ☐    5  Manually excluded                                   │
│  ─────────────────────────────────────────────────────────── │
│  Total to import: 100 rows                                   │
│                                                              │
│  ℹ️  Realized gain: All 100 rows will show "Pending cost     │
│  basis" after import. Declare a cost method on each          │
│  security's detail page to compute gains.                    │
│                                                              │
│  ℹ️  Ledger replay will run after commit for {N} securities.  │
│  Any prior inventory warnings that this batch resolves will  │
│  be cleared automatically.                                   │
│                                                              │
│  ⚠️  1 row matches an existing sale by dedup key — will be  │
│  imported with a "possible duplicate" flag.                  │
│                                                              │
│  [ ← Back to Preview ]       [ Import 100 rows → ]          │
└──────────────────────────────────────────────────────────────┘
```

---

## 14. Post-Import Account Assignment

Identical to dividend and purchase import designs:
- Per-record inline popover from Movements table
- Bulk via row-select toolbar
- Import-batch banner shortcut ("Assign all {N} to a broker account →")
- "(Unassigned)" filter on Movements page

---

## 15. Alias Mapping — Shared with Dividend and Purchase Imports

The company → security alias store is global and account-agnostic, shared across all three import types. A mapping made during dividend import ("TELEFÓNICA S.A." → TEF) is pre-resolved automatically in sales preview. The alias mapper modal is identical across all types.

---

## 16. API Surface — Sales-Specific Additions

Extends the API surface from `rusty-dividend-import-ux.md §11` and `rusty-purchase-import-ux.md §15`.

```
POST   /api/portfolio/import/execute
       Same endpoint; sales rows distinguished by row.classification: 'SELL' | 'ZERO_PROCEED_SALE'
       Creates ledger_txn: SELL with gross_proceeds_eur, commission_eur, shares, sale_date.
       net_cash_eur computed server-side and stored; unit_price_derived NOT stored.

GET    /api/portfolio/positions/inventory
       ?security_id=&at_date=
       Returns reconstructed inventory at the given date using all committed records.
       Used by preview to compute per-row inventory state.

GET    /api/portfolio/positions/inventory/batch
       Body: { security_id, dates: string[] }
       Batch variant for preview (avoids N individual calls for N rows).

POST   /api/portfolio/ledger/replay
       ?security_ids[]=&triggered_by_batch_id=
       Triggers chronological replay for the listed securities.
       Returns: { replayed: SecurityReplayResult[] }
       Called automatically after each commit; also callable manually.

GET    /api/portfolio/movements/:id/realized-gain
       Returns RG status: { status: 'unavailable' | 'partial' | 'computed', value?: number,
                             coverage_pct?: number, cost_method?: string }

PUT    /api/portfolio/securities/:ticker/cost-method
       Body: { method: 'fifo' | 'lifo' | 'average' | 'specific_lot' }
       Declares cost method and triggers RG recomputation for all sales of the security.
       Returns: { recomputed_count: number, rg_summary: RgSummary }

GET    /api/portfolio/ca-events
       Extended: status=negative_inventory now also valid for filtering sale records.

GET    /api/portfolio/reconciliation/summary
       Returns counts across all pending types (negative_inventory, pending_cost_basis,
       no_cost_method, pending_event_link, rights_pending) grouped by year and type.
       Used by the reconciliation queue header.
```

---

## 17. TypeScript DTO Outlines — Sales-Specific

```typescript
// ─── Sale import row classification ──────────────────────────────────────────

type SaleClassification =
  | 'SELL'
  | 'ZERO_PROCEED_SALE'
  | 'ZERO_QUANTITY'
  | 'NEGATIVE_VALUE'
  | 'PARSE_ERROR'
  | 'FUTURE_DATE'
  | 'DATE_YEAR_MISMATCH';

type SaleWarningCode =
  | 'negative_inventory'
  | 'rg_unavailable'                // informational; always present at import time
  | 'commission_exceeds_proceeds'
  | 'net_cash_zero'
  | 'zero_proceed_sale'
  | 'split_sale_proximity'
  | 'date_ambiguous'
  | 'future_date'
  | 'date_year_mismatch'
  | 'zero_quantity'
  | 'negative_quantity'
  | 'negative_total'
  | 'negative_commission'
  | 'parse_error'
  | 'duplicate'
  | 'unresolved_security';

// ─── Normalized sale row from parse step ─────────────────────────────────────

interface NormalizedSaleRow {
  row_index: number;
  year: number | null;
  company_raw: string;
  sale_date: string | null;                // ISO YYYY-MM-DD
  // Source fields stored as decimal strings (lossless)
  shares: string | null;                   // "80.000000"
  gross_proceeds_eur: string | null;       // "Total Venta" — authoritative
  commission_eur: string | null;
  // Derived fields (display only; never stored as source)
  net_cash_eur: string | null;             // gross_proceeds − commission
  unit_price_derived: string | null;       // gross_proceeds ÷ shares; labeled "Calculated"
  // Inventory check result
  inventory_at_date?: number | null;       // null = no committed data
  inventory_shortfall?: number | null;     // null if no shortfall
  // Classification
  classification: SaleClassification;
  split_sale_proximity_row_index?: number; // partner row if proximity warning fires
  // Resolution
  resolved_security_id?: string;
  resolved_security_ticker?: string;
  via_saved_alias?: boolean;
  // Status
  warnings: SaleWarningCode[];
  status: 'ready' | 'warning' | 'zero_proceed' | 'blocking';
  dedup_key?: string;
}

// ─── Sale import submission row ───────────────────────────────────────────────

interface SaleRowSubmission {
  row_index: number;
  classification: SaleClassification;
  security_id: string;
  sale_date: string;                       // ISO
  shares: string;                          // decimal string; lossless
  gross_proceeds_eur: string;              // source truth
  commission_eur: string;
  // net_cash_eur is computed server-side; not in submission
  warnings: SaleWarningCode[];
  dedup_key: string;
}

// ─── Ledger replay result ─────────────────────────────────────────────────────

interface SecurityReplayResult {
  security_id: string;
  ticker: string;
  inventory_warnings_cleared: number;
  inventory_warnings_remaining: number;
  rg_newly_computed: number;              // sales that went from UNAVAILABLE to computed
  rg_partial_resolved: number;           // sales that went from PARTIAL to fully computed
}

// ─── Cost method declaration ──────────────────────────────────────────────────

type CostMethod = 'fifo' | 'lifo' | 'average' | 'specific_lot';

interface SetCostMethodRequest {
  method: CostMethod;
}

interface RgStatus {
  status: 'unavailable' | 'partial' | 'computed';
  value?: string;                          // decimal string; EUR
  coverage_pct?: number;                   // 0–100; present when partial
  cost_method?: CostMethod;
  lots_used?: number;
}
```

---

## 18. Route Placement and Phase 1c Scope

### Routes

| Route | Purpose |
|---|---|
| `/portfolio/import` | Shared wizard entry — now 3-type selector |
| `/portfolio/import?type=sales` | Deep-link directly to sales wizard |
| `/portfolio/movements?status=pending_reconciliation&year=2026` | Unified reconciliation queue |
| `/portfolio/movements?status=pending_reconciliation&year=2026&type=negative_inventory` | Queue filtered to inventory warnings |
| `/portfolio/movements?status=pending_reconciliation&year=all&type=pending_cost_basis` | All-year pending-gain queue |
| `/portfolio/securities/:ticker` | Cost method declaration (not a wizard page) |

### Phase Placement

| Phase | Scope |
|---|---|
| **Phase 1 (MVP)** | Manual BUY/SELL/DIVIDEND/DIV+STOCK forms |
| **Phase 1.5** | Dividend CSV import (8-column), rights reconciliation |
| **Phase 1b** | Purchase CSV import (7-column), corporate-action-pending flow |
| **Phase 1c (this document)** | Sales CSV import (6-column), inventory warnings, RG pending state, ledger replay, cost method declaration |
| **Phase 2 (deferred)** | Charts, Economics integration, fiscal export, broker-native imports |

**Phase 1c dependencies:** the inventory check at preview time benefits from committed purchase records. It functions correctly without them (shows "— no prior data") — Phase 1c does not technically require Phase 1b to ship first, but the user experience is better when purchase history exists. Recommended sequence: 1.5 → 1b → 1c.

**Wizard shell:** the type selector update (3 cards) requires a trivial additive change to the existing wizard entry component. Dividend and purchase wizard flows are unchanged.

**Ledger replay** is a shared backend service triggered after any commit of any import type. Its results surface in the post-commit banner regardless of which wizard was used.

---

## Summary

**Source format:** 6 fixed columns (Año, Empresa, Fecha venta, Acciones, Comisión, Total Venta). All EUR. `Total Venta` is the authoritative source fact. `Net Cash = Total Venta − Comisión` and `Unit Price = Total Venta ÷ Acciones` are always derived — never stored as source, never back-propagated, always labeled "Calculated" in UI.

**Import type selector:** updated to 3 cards (Dividends / Purchases / Sales). Deep-link `/portfolio/import?type=sales`.

**Row classification:** `SELL` (normal), `ZERO_PROCEED_SALE` (zero total + zero commission, non-blocking with explicit acknowledgement). Blocking for zero/negative quantity, negative total, negative commission, future date, date/year mismatch. **No auto-reclassification of small or zero proceeds.**

**Preview:** Gross Sale · Commission · Net Cash (bold, amber/red if ≤ 0) · Unit Price *(Calculated, italicised)* · Inventory state · `Pending cost basis`. Inventory check runs server-side against committed records.

**Inventory warnings are always non-blocking.** Missing purchase history is expected during incremental import. Warnings clear automatically via ledger replay when purchases are committed later.

**Ledger replay** triggers after every committed batch for affected securities: re-evaluates inventory, clears resolved warnings, computes newly available RG. Post-commit banner shows per-security replay results including cleared warnings and newly available gains.

**Realized gain = `Pending cost basis`** until a cost method is declared on the security detail page and sufficient lots exist. Partial coverage shows computed portion + `Pending cost basis` remainder.

**Reconciliation queue** extended with `Negative inventory`, `Pending gain`, and `Cost basis` filter options. Default year = 2026; older records stored and accessible by year selection; not shown in default view.

**Shared:** alias mapping, dedup behavior (intra-batch exact duplicate = block; cross-batch = warn), account-optional/no-warning, partial import mechanics, post-import bulk account assignment.

**Phase 1c** — extends the shared wizard with a third import type. Recommended after Phase 1b. Ledger replay is shared infrastructure that benefits all previously committed records retroactively.
