# Purchase History CSV Import — UX & Wizard Design

**Author:** Rusty (Agent Dev — frontend/integration)  
**Date:** 2026-09-05  
**Directive:** `copilot-directive-20260905T170353+0200.md`  
**Depends on:** `rusty-dividend-import-ux.md` (wizard framework, alias mapping, reconciliation queue, account-optional rules)  
**Depends on:** `livingston-scrip-rights-topup-clarification.md` (`ca_event` / `ca_leg` domain model)  
**Status:** Design only — no production code. No user financial data reproduced.  
**Phase:** 1b — parallel to Phase 1.5 (dividend import); both share the import wizard framework.

---

## 1. Source Format: Fixed 7-Column Purchase CSV

The import consumes **only the first 7 columns**. Columns beyond position 7 are silently ignored.

| Position | Column name (Spanish) | Normalized field | Type | Notes |
|---|---|---|---|---|
| 1 | `Año` | `year` | integer | 4-digit year; cross-validated against purchase date |
| 2 | `Empresa` | `company_raw` | string | Free text; alias mapping shared with dividend import |
| 3 | `Fecha compra` | `purchase_date_raw` | string → date | DD/MM/YYYY Spanish locale |
| 4 | `Valor compra` | `price_per_share` | high-precision decimal | Spanish comma format; minimum 8 decimal places stored |
| 5 | `Acciones` | `shares` | high-precision decimal | Fractional shares; minimum 6 decimal places stored |
| 6 | `Total (€)` | `total_eur` | high-precision decimal | Total purchase cost in EUR |
| 7 | `Comisión` | `commission_eur` | decimal | Commission in EUR; 0 or positive |

**Currency:** all amount fields in this format are denominated in EUR. There is no per-row currency column — the batch inherits EUR as its native currency. The `total_eur` column is the authoritative cash outflow before commission; it is not a computed field for import purposes (the arithmetic check verifies it, but it is stored as provided).

**Precision contract:**
- `price_per_share`, `shares`, `total_eur`, `commission_eur` are parsed from Spanish decimal notation and stored at **full source precision** — no rounding or truncation at any point from parse through storage.
- Minimum display: 2 decimal places in tables; full precision on row expand and in the detail panel.
- The arithmetic check uses the stored values; floating-point tolerance is applied only to the warning threshold (see §5).

---

## 2. Shared Wizard Entry: Import Type Selection

The existing `/portfolio/import` wizard gains an **import type selection screen** as its new first step, displayed before the upload step.

```
┌──────────────────────────────────────────────────────────────┐
│  Import Historical Records                                   │
│                                                              │
│  What are you importing?                                     │
│                                                              │
│  ┌──────────────────────┐  ┌──────────────────────┐         │
│  │  💰 Dividends        │  │  🛒 Purchases         │         │
│  │                      │  │                      │         │
│  │  8 columns:          │  │  7 columns:          │         │
│  │  Año · Empresa ·     │  │  Año · Empresa ·     │         │
│  │  Fecha de cobro ·    │  │  Fecha compra ·      │         │
│  │  Importe Bruto ·     │  │  Valor compra ·      │         │
│  │  Importe Neto ·      │  │  Acciones ·          │         │
│  │  Importe en Derechos │  │  Total (€) ·         │         │
│  │  Retención Origen ·  │  │  Comisión            │         │
│  │  Retención Destino   │  │                      │         │
│  └──────────────────────┘  └──────────────────────┘         │
│         [ Select ]                  [ Select ]              │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

Selecting a type proceeds to the Upload/Paste step (renamed "Step 0" — unchanged from the dividend import design). The breadcrumb adapts: `Type → Upload → Metadata → Preview → Confirm`.

Both import types share:
- The Upload/Paste step (drag/drop + textarea)
- The Batch Metadata step structure (source currency required; account optional)
- The alias mapper (company → security, global, account-agnostic)
- The partial-import mechanics (row checkboxes, bulk exclude, exact counts)
- The reconciliation queue (year-filtered, default 2026)
- The account-optional rule (no warning for unassigned account)

---

## 3. Parse Strategy for Purchases

### 3.1 Reused from Dividend Parser

- Delimiter auto-detection: tab → semicolon → comma (with ambiguity guard)
- Spanish date parsing: `DD/MM/YYYY` strict; year cross-validation
- Spanish number parsing: comma as decimal, period as thousands; full regex pass
- XLSX handling: SheetJS client-side, sheet 0 only

### 3.2 High-Precision Number Parsing

`Valor compra`, `Acciones`, `Total (€)`, `Comisión` require lossless parsing:

1. Strip thousands separators (`.`)
2. Replace decimal separator (`,` → `.`)
3. Parse using a decimal-aware routine (not native JS `parseFloat` — that limits to 15–17 significant digits). A `Decimal` string type is used in transport and storage; the client sends the raw cleaned string (e.g. `"24.75000000"`) and the server parses it as a decimal with the backend's arbitrary-precision library.
4. Display logic: show at least 2dp in table columns; the full string is always preserved. On row expand, display the exact stored string.

**Why this matters for fractional shares:** a purchase of `9.123456` shares at `€24.750000` per share produces a total of `€225.804636` — truncating shares to 2dp would produce `€225.75`, a visible discrepancy in the arithmetic check.

### 3.3 Batch Metadata for Purchases

Step 1 — Batch Metadata for purchase imports:

```
┌──────────────────────────────────────────────────────────────┐
│  Step 1 of 3 — Batch Metadata (Purchases)                    │
│  {N} rows parsed · {W} with warnings                        │
│                                                              │
│  ── Required ──────────────────────────────────────────────  │
│  Source currency *       [EUR (fixed for this format)]       │
│                          "All amounts in this file are EUR"  │
│                                                              │
│  ── Optional ──────────────────────────────────────────────  │
│  Broker / Account        [(none) ▾ | existing accounts...]  │
│                          "Can be assigned later — leaving    │
│                          blank does not affect data quality" │
│                                                              │
│  [ ← Back ]                              [ Preview → ]      │
└──────────────────────────────────────────────────────────────┘
```

Source currency is fixed to EUR for this format — the field is shown as read-only (not a dropdown) with an explanatory note. No FX rate input is needed: the `Total (€)` column is the EUR amount. No origin-WHT-country field (not applicable for purchases).

---

## 4. Row Classification

The preview assigns each row a **primary classification** before any user action. Classification is deterministic from the parsed field values and does not require security resolution.

### 4.1 Classification Rules (evaluated in order)

| Priority | Name | Trigger | Status chip | Maps to |
|---|---|---|---|---|
| 1 | **`Corporate action pending`** | `price_per_share = 0` AND `total_eur = 0` AND (`commission_eur = 0` OR missing) AND `shares > 0` | `🔵 Corp. action` | `ca_leg: SHARE_ACQUISITION` (pending reconciliation) |
| 2 | **`BUY`** | `price_per_share > 0` AND `shares > 0` AND `total_eur > 0` | `✅ Ready` or `⚠️ Warning` | `ledger_txn: BUY` |
| 3 | **`All-zero`** | All of price, shares, total, commission = 0 | `🔴 Blocking` | Not imported |
| 4 | **`Parse error`** | Any field could not be parsed | `🔴 Blocking` | Not imported |
| 5 | **`Ambiguous`** | Partial zeros not matching rule 1 or 2 (e.g., price > 0 but total = 0, or shares = 0 but total > 0) | `🔴 Blocking` | Not imported without user action (must exclude or fix source) |

**Rule 1 is strict:** ALL three conditions (price = 0, total = 0, commission = 0) must hold simultaneously for a positive-share row to be classified as `Corporate action pending`. A row with `price = 0, total = 0, commission = 1.50` is `Ambiguous` (commission paid but no purchase price — unusual, requires investigation).

### 4.2 "Possible Top-Up" Suggestion (Advisory Only)

After classification, the system scans within each company group for proximity between `BUY` rows and `Corporate action pending` rows:

**Trigger:** a `BUY` row and a `Corporate action pending` row share the same `company_raw` (or same resolved security after alias mapping), and their `purchase_date` values are within **30 calendar days** of each other.

**Effect:** the `BUY` row receives an advisory chip `💡 Possible top-up` in addition to its normal status chip. The classification **remains `BUY`** — the chip is informational only.

**What it signals:** "This paid purchase is temporally close to a zero-cost share receipt for the same company. It may be a cash top-up paid to acquire whole shares as part of a scrip/rights event." The user decides whether to link them during reconciliation.

**No automatic reclassification.** The chip never changes the row's status. A `BUY` row with `💡 Possible top-up` is still imported as a `ledger_txn: BUY` unless the user manually reclassifies it during reconciliation.

**Proximity threshold:** 30 days is advisory and may be tightened. Future enhancement: user-adjustable. For MVP, 30 days is hardcoded.

---

## 5. Preview Table Structure

One row per parsed CSV row. The preview is the same full-width table as the dividend import.

| # | Column | Description |
|---|---|---|
| — | Checkbox | Include/exclude (checked by default; auto-unchecked for blocking) |
| — | Classification chip | See §4: `✅ BUY` · `🔵 Corp. action` · `🔴 Blocking` · `⚠️ Warning` |
| — | Advisory chip | `💡 Possible top-up` if proximity rule fires (appended to chip list) |
| 1 | Year | Parsed integer |
| 2 | Security | Resolved security OR ⚠️ "Unknown: {company_raw}" with [Map] button |
| 3 | Purchase Date | Normalized YYYY-MM-DD display |
| 4 | Price/Share | Compact display (4dp) · expand for full precision |
| 5 | Shares | Compact display (4dp) · expand for full precision |
| 6 | Total EUR | Full precision |
| 7 | Commission | Full precision |
| — | Cash outflow | `total_eur + commission_eur` (computed, read-only) |
| — | Arithmetic | `✅` or `⚠️ Δ {amount}` for price×shares vs total check |
| — | Account | "(unassigned)" in muted text — **no warning chip** |
| — | Warnings | Chip list (see §6) |

### 5.1 Precision Display Pattern

**Compact mode (table):** show 4 decimal places. If the value has more non-zero digits beyond position 4, append `…` indicator. Hovering the cell shows a tooltip with the full-precision value.

```
Price:   24.7500…    → tooltip: "24.75000000"
Shares:   9.1234…    → tooltip: "9.123456"
Total:  225.8046…    → tooltip: "225.804636"
```

**Expand mode (row details):** show full stored string for all numeric fields, monospace font.

**Accessibility:** tooltip is also accessible via `aria-describedby` on the cell — screen readers read the full-precision value from a visually hidden span.

### 5.2 Corporate Action Rows in Preview

`Corporate action pending` rows show with amber-tinted row background (lighter than blocking red; same amber as dividend rights rows). Columns 4 (Price) and 6 (Total) display `—` (zero, stored as "0.000000" but rendered as dash for clarity). Column 7 (Commission) also shows `—`.

The row inline-expands to show:
```
┌─────────────────────────────────────────────────────────────────┐
│ 🔵 Corp. action pending — 2024 | {TICKER} | 15/03/2024         │
│  Shares received: 9.000000                                      │
│  Price, total, commission: all zero                             │
│  ── Suggested match (see §7) ──────────────────────────────────│
│  💡 Possible dividend/rights event: {Ticker} ~2024-03-07        │
│     [Link to existing event] [Create new event] [Ignore]        │
└─────────────────────────────────────────────────────────────────┘
```

---

## 6. Warning Taxonomy — Purchase-Specific

Inherits all dividend import warnings where applicable (`parse_error`, `date_year_mismatch`, `all_zero`, `duplicate`, `unresolved_security`). Adds:

| Code | Severity | Trigger | User message |
|---|---|---|---|
| `arithmetic_mismatch` | warn | `|price × shares − total_eur| > max(0.005, 0.0001 × total_eur)` | "Price × shares ≠ Total (diff: {amount}). Review or confirm if rounding is expected." |
| `corporate_action_pending` | warn (importable) | Classification rule 1 | "Zero-cost share receipt — not classified as BUY. Links to reconciliation queue for event association." |
| `possible_top_up` | info (advisory) | Proximity rule within same company | "Paid purchase is within 30 days of a zero-cost receipt for the same security. May be a cash top-up." |
| `ambiguous_zeros` | **block** | Partial-zero combination not matching rule 1 or 2 | "Combination of zero and non-zero values is ambiguous. Cannot classify as BUY or corporate action. Exclude or fix source." |
| `commission_only` | **block** | `price = 0, total = 0, shares = 0, commission > 0` | "Commission with no purchase — cannot classify. Exclude or investigate." |
| `negative_value` | **block** | Any numeric field is negative | "Negative value in {column}. Purchase file should not contain negative amounts. Exclude or fix source." |

**Administrative vs data-quality separation** (same rule as dividend import): unassigned account is not a warning code and does not affect row status.

**`arithmetic_mismatch` is non-blocking for BUY rows.** The user can import a row with a small discrepancy. Tolerance is proportional: `max(0.005, 0.0001 × total_eur)` — a €10,000 purchase tolerates up to €1.00 of rounding; a €10 purchase tolerates up to €0.005. This prevents over-flagging on fractional-share purchases where the source file rounds to 2dp. The stored values are always those from the source columns verbatim; the check is advisory.

**`corporate_action_pending` is non-blocking** — the row is importable in an incomplete state, the same as rights-pending dividend rows.

---

## 7. Corporate Action Pending — Event Matching and Linking

### 7.1 Suggested Match Logic

After security resolution (alias mapping), the system searches existing `ca_event` records (and other `Corporate action pending` rows in the same batch) for likely matches:

**Match criteria (all must hold):**

1. Same `security_id` (after alias resolution)
2. `ca_event.payment_date` OR `ca_event.ex_dividend_date` within **60 calendar days** of the corporate-action-pending row's `purchase_date`
3. `ca_event.ca_type` ∈ {`DIVIDEND_WITH_SCRIP`, `SCRIP_DIVIDEND`}

**Match result:** zero, one, or multiple candidate events. All candidates are shown; none is auto-selected.

**Within-batch matching:** if the same import batch contains both a `Corporate action pending` purchase row AND a `rights_pending` dividend row for the same security around the same date, they are cross-suggested to each other in the preview — a banner shows: "These two rows may belong to the same event — review in reconciliation queue."

### 7.2 Match Suggestion Display (Preview)

The expanded row section (§5.2) shows all candidates in a compact table:

```
Suggested dividend/rights events:
┌──────────────────────────────────────────────────────────────┐
│ Date       | Type              | Gross EUR | Status          │
│ 2024-03-07 | DIVIDEND_W_SCRIP  | 209.79    | Rights pending  │
│ 2023-09-15 | DIVIDEND_W_SCRIP  |  98.45    | Valid           │
│ [+ Search for other events]                                  │
└──────────────────────────────────────────────────────────────┘
[Link to selected event ▾]   [Create new ca_event]   [Import unlinked]
```

**"Link to selected event"** is a dropdown allowing the user to pick one candidate from the table. It does NOT immediately execute — it stages the link for the import step. At import time, the server attaches the new `SHARE_ACQUISITION` leg to the chosen `ca_event`.

**"Create new ca_event"** stages the creation of a new parent event (minimal: `ca_type`, `security_id`, `payment_date`). The new event is created atomically with the leg at import time.

**"Import unlinked"** stages the row as a standalone `SHARE_ACQUISITION` leg with `ca_event_id = null`. It enters the reconciliation queue with status `pending_event_link`.

### 7.3 One Dividend Event to Multiple Rows

A single `ca_event` may receive multiple `SHARE_ACQUISITION` and/or `CASH_TOP_UP` legs from this import. This happens when:
- The company issued shares across multiple sub-tranches for the same event
- A top-up payment row (`💡 Possible top-up` BUY row) is manually reclassified and linked to the same event

**Preview grouping:** when multiple rows in the batch are linked to the same staged `ca_event`, the preview table groups them visually with a shared left-border connector:

```
🔵 Corp. action  | REP | 2024-03-15 | 9 shares | → ca_event 2024-03-07
💡 BUY → Top-up? | REP | 2024-03-18 | (none)   | € 4.95 commission only
```

The user can drag (or use a [Link →] button) to explicitly associate the BUY row with the same `ca_event`. When linked, the BUY row's classification becomes `🟡 Staged as top-up` — it will be imported as a `CASH_TOP_UP` leg rather than a `ledger_txn: BUY`. This is the **only** automatic reclassification path, and it requires explicit user action (never proximity alone).

---

## 8. Step 3 — Review & Import (Purchase Summary)

```
┌──────────────────────────────────────────────────────────────┐
│  Step 3 of 3 — Review & Import (Purchases)                   │
│                                                              │
│  Account:         (unassigned — assign after import)         │
│  Source currency: EUR (all amounts)                          │
│                                                              │
│  ─── Import summary ────────────────────────────────────────  │
│                                                              │
│  ✅  98  BUY rows — will create ledger_txn: BUY              │
│  🟡   2  Staged as top-up — will create ca_leg: CASH_TOP_UP  │
│  🔵   7  Corp. action pending — imported incomplete;         │
│           linked to {N} events, {M} unlinked (queue)         │
│  ⚠️   5  Warning rows — imported with warnings attached      │
│  🔴   3  Blocking rows — excluded                            │
│  ☐    4  Manually excluded by you                            │
│  ─────────────────────────────────────────────────────────── │
│  Total to import: 112 rows                                   │
│                                                              │
│  ⚠️  Duplicate check: 1 row matches an existing BUY by       │
│      dedup key. Will be imported with "possible duplicate"   │
│      flag. Review in Movements after import.                 │
│                                                              │
│  [ ← Back to Preview ]        [ Import 112 rows → ]         │
└──────────────────────────────────────────────────────────────┘
```

---

## 9. Duplicate Detection for Purchases

### 9.1 Dedup Key

Account excluded (same principle as dividend import):

```
{security_id}|BUY|{purchase_date}|{shares}|{total_eur}
```

For `Corporate action pending` rows:

```
{security_id}|SHARE_ACQ|{purchase_date}|{shares}
```

**Shares (not price) in the key** for corporate action rows because price is always zero — using total or price would produce identical keys for all zero-cost rows from the same company on the same date if shares differ.

### 9.2 Server-Side Check

Same pattern as dividend import: `POST /api/portfolio/import/check-duplicates` with just the dedup keys, no financial values. Returns matched keys. Duplicates are warned, not auto-skipped.

---

## 10. Reconciliation Queue — Purchases

### 10.1 Extended Queue for Purchase Records

The existing reconciliation queue at `/portfolio/movements?status=pending_reconciliation&year=2026` is extended with a **type filter** to distinguish between the two sources of pending items:

```
Pending reconciliation — 2026

Year: [2026 ▾]   Type: [All ▾ | Rights/Dividends | Corp. action | Cost basis]

  ✅  0  Rights/dividend pending  (from dividend import)
  🔵  4  Corporate action pending (from purchase import, unlinked or incomplete)
  🟡  2  Cost basis pending       (from either import)
```

**`Corporate action pending` statuses in the queue:**
- `pending_event_link` — SHARE_ACQUISITION leg exists but has no parent `ca_event` (imported unlinked)
- `pending_legs` — leg is attached to a `ca_event` but the event is still incomplete (e.g., no RIGHTS_SOLD leg yet)
- `pending_cost_basis` — event structure is complete but cost basis method is UNKNOWN

**Year filter defaults to 2026**, same as the dividend reconciliation. Pre-2026 corporate-action-pending rows are stored; selecting an older year surfaces them. They do not appear in the default view and do not interrupt normal use.

### 10.2 Reconciliation Detail Form — Corporate Action Pending

Opens on "[Complete →]" for a corporate-action-pending row. Slide-over, same UX pattern as the dividend reconciliation form.

```
─── Source data (from import, read-only) ────────────────────
  Year:             {Año}
  Company:          {Empresa} → {Resolved security}
  Purchase date:    {Fecha compra}
  Price/share:      0.000000 (zero-cost acquisition)
  Shares:           {exact stored value, full precision}
  Total EUR:        0.000000
  Commission:       0.000000

─── Event association ────────────────────────────────────────
  Link to ca_event:   [search/select existing event]
                      OR [Create new ca_event]
                      OR [Keep unlinked — save for later]

  If linked:
    Parent event:     {Ticker} — {payment_date} [{ca_type}]
    Legs on event:    {list of existing legs}
    This leg adds:    SHARE_ACQUISITION ({shares} shares)

─── Share acquisition leg ────────────────────────────────────
  Shares received:    {pre-filled from import, editable}
  FMV per share:      [decimal — fair value at ex-date, optional]
  FMV reference date: [date picker, optional]
  FMV source:         [BROKER_STATEMENT ▾ | COMPANY_CIRCULAR | MANUAL]
  Cost basis method:  [FMV_EX_DATE ▾ | ZERO | TOP_UP_ONLY |
                       FMV_PLUS_TOP_UP | MANUAL_OVERRIDE | UNKNOWN]
  Cost basis note:    [textarea, required for MANUAL_OVERRIDE / UNKNOWN]

─── Associated top-up (if a BUY row was linked in preview) ──
  [Read-only card showing the staged CASH_TOP_UP leg]
  Amount:   {total_eur + commission_eur}
  Note:     "Cash top-up fact from broker statement.
             Does NOT automatically set cost basis —
             see Cost basis method above."
  [Edit top-up] [Remove association]

─── Notes ────────────────────────────────────────────────────
  Notes    [optional textarea]

─── Arithmetic / event check (live) ─────────────────────────
  Shares from this leg:    {shares}
  Shares on event total:   {sum of all SHARE_ACQUISITION legs}
  Expected from scrip ratio: {computed if ratio known, else —}
  ✅ / ⚠️ Consistent / Mismatch — advisory only

[ Cancel ]     [ Save — link event and set cost basis ]
```

**Partial save** (same rule as dividend reconciliation): if cost basis method is `UNKNOWN`, the event moves to `pending_cost_basis`. The `pending_event_link` state is cleared once any `ca_event` association is made.

---

## 11. BUY Row → Top-up Manual Reclassification

When a user decides a `BUY` row is actually a cash top-up for a corporate action (identified visually via the `💡 Possible top-up` chip or independently), they can reclassify it from the preview or from the Movements list post-import.

**From preview:** click [Link →] on the BUY row and select a `Corporate action pending` row or an existing `ca_event`. The row's chip changes to `🟡 Staged as top-up`. On import it creates a `ca_leg: CASH_TOP_UP` instead of `ledger_txn: BUY`.

**Post-import reclassification:** the Movements page provides a "Reclassify" action on any BUY row, opening a slide-over:

```
Reclassify: BUY → Cash Top-Up
This will:
 - Void the existing BUY transaction (reason: "Reclassified as cash top-up")
 - Create a CASH_TOP_UP leg on:
   [select ca_event — search or paste event id]
 - The CASH_TOP_UP amount will be: {total_eur + commission_eur}
 - This does NOT set the cost basis of the received shares.

[ Cancel ]   [ Reclassify → ]
```

This always goes through a void + create pair (audit trail preserved). The old BUY record is soft-voided with reason "Reclassified as cash top-up linked to ca_event {id}".

---

## 12. Partial Import and Exclusion Controls

Identical mechanics to the dividend import:

1. Individual row checkbox (uncheck to exclude)
2. "Exclude all Blocking rows" (auto-applied on first preview load)
3. Bulk status chips: "Select all BUY" / "Deselect all Corp. action" / etc.

Additionally, for purchase imports:
- **"Exclude all Possible top-up rows"** — a convenience action to defer all proximity-flagged BUY rows for manual review, importing clean BUYs first.
- **"Review Corp. action rows"** — scrolls the preview to the first `Corporate action pending` row and highlights the event-matching section.

Step 3 always shows exact counts per category before the import executes.

---

## 13. Post-Import Account Assignment

Identical to the dividend import design (§7a of `rusty-dividend-import-ux.md`):
- Per-record inline popover from Movements table
- Bulk via row-select toolbar
- Import-batch banner shortcut
- "(Unassigned)" filter on Movements page

`CASH_TOP_UP` legs inherit the account assignment of their parent `ca_event` at creation time (or remain unassigned if the event has none). A top-up reclassification always prompts account assignment as an optional step.

---

## 14. Alias Mapping — Shared with Dividend Import

The alias table (`company_raw_normalized → security_id`) is the same server-side store used by the dividend import. No separation by import type. If the user has already mapped "TELEFÓNICA S.A." → TEF when importing dividends, it is pre-resolved automatically in the purchase import preview.

The alias mapper modal is identical. "Create new security" flow is identical.

---

## 15. API Surface — Purchase-Specific Additions

Extends the API surface defined in `rusty-dividend-import-ux.md §11`.

```
POST   /api/portfolio/import/execute
       Already defined. Purchase rows are submitted in the same endpoint.
       Row type distinguished by row.classification:
         'BUY' → creates ledger_txn: BUY
         'CORPORATE_ACTION_PENDING' → creates ca_leg: SHARE_ACQUISITION
                                       (with optional ca_event_id)
         'STAGED_TOP_UP' → creates ca_leg: CASH_TOP_UP under ca_event_id

POST   /api/portfolio/import/check-duplicates
       Already defined. No changes needed — account-free dedup keys work for both types.

GET    /api/portfolio/ca-events?status=pending_event_link|pending_legs|pending_cost_basis
                                &year=2026&ca_type=SHARE_ACQUISITION
       Extended status: 'pending_event_link' is new (purchase-specific state).

PATCH  /api/portfolio/ca-events/legs/:leg_id/link-event
       Body: { ca_event_id: string | null }
       Links a standalone SHARE_ACQUISITION leg to a parent ca_event.
       Triggers validation_status recomputation on the event.

POST   /api/portfolio/movements/:id/reclassify-as-top-up
       Body: { ca_event_id: string }
       Atomically voids the BUY ledger_txn and creates a CASH_TOP_UP leg.
       Returns: { voided_id, new_leg_id }

GET    /api/portfolio/ca-events/:id/suggest-matches
       ?security_id=&date=&window_days=60
       Returns candidate ca_events for a corporate-action-pending row.
       Used to populate the event-matching section in preview and reconciliation form.
```

---

## 16. TypeScript DTO Outlines — Purchase-Specific

```typescript
// ─── Purchase import row classification ──────────────────────────────────────

type PurchaseClassification =
  | 'BUY'
  | 'CORPORATE_ACTION_PENDING'
  | 'STAGED_TOP_UP'               // user manually linked a BUY to a ca_event as top-up
  | 'AMBIGUOUS'                   // blocking — partial zeros, cannot classify
  | 'ALL_ZERO'                    // blocking
  | 'PARSE_ERROR';                // blocking

type PurchaseWarningCode =
  | 'arithmetic_mismatch'
  | 'corporate_action_pending'    // non-blocking, importable
  | 'possible_top_up'             // advisory only, never blocking
  | 'ambiguous_zeros'             // blocking
  | 'commission_only'             // blocking
  | 'negative_value'              // blocking
  | 'unresolved_security'         // blocking
  | 'date_year_mismatch'
  | 'duplicate'
  | 'parse_error'
  | 'all_zero';

// ─── Normalized purchase row ──────────────────────────────────────────────────

interface NormalizedPurchaseRow {
  row_index: number;
  year: number | null;
  company_raw: string;
  purchase_date: string | null;       // ISO YYYY-MM-DD
  // All numeric fields stored as decimal strings (lossless precision)
  price_per_share: string | null;     // "24.75000000"
  shares: string | null;              // "9.123456"
  total_eur: string | null;
  commission_eur: string | null;
  // Derived
  cash_outflow_eur: string | null;    // total_eur + commission_eur
  arithmetic_delta: string | null;    // |price×shares − total|
  // Classification
  classification: PurchaseClassification;
  possible_top_up_for_row_index?: number;  // index of the matched zero-cost row
  // Resolution
  resolved_security_id?: string;
  resolved_security_ticker?: string;
  via_saved_alias?: boolean;
  // Staging
  staged_ca_event_id?: string;        // set when user links to an event in preview
  staged_ca_event_create?: boolean;   // true if user chose "Create new ca_event"
  // Status
  warnings: PurchaseWarningCode[];
  status: 'ready' | 'warning' | 'corporate_action_pending' | 'staged_top_up' | 'blocking';
  dedup_key?: string;
}

// ─── Purchase import submission row ──────────────────────────────────────────

interface PurchaseRowSubmission {
  row_index: number;
  classification: PurchaseClassification;
  security_id: string;
  purchase_date: string;             // ISO
  // Stored as strings to preserve precision
  price_per_share: string;
  shares: string;
  total_eur: string;
  commission_eur: string;
  // For CORPORATE_ACTION_PENDING and STAGED_TOP_UP
  ca_event_id?: string | null;       // null = import unlinked
  ca_event_create_payload?: {        // present if user chose "Create new"
    ca_type: string;
    payment_date: string;
  };
  warnings: PurchaseWarningCode[];
  dedup_key: string;
}

// ─── Reclassify BUY as top-up ────────────────────────────────────────────────

interface ReclassifyAsTopUpRequest {
  ca_event_id: string;
  void_reason?: string;             // default: "Reclassified as CASH_TOP_UP"
}

// ─── Event suggestion result ─────────────────────────────────────────────────

interface CaEventSuggestion {
  ca_event_id: string;
  security_ticker: string;
  payment_date: string;
  ca_type: string;
  validation_status: string;
  existing_legs: Array<{ leg_type: string; quantity?: string }>;
  date_delta_days: number;          // |purchase_date − ca_event.payment_date|
}
```

---

## 17. Route Placement and Phase 1b Scope

### Routes

| Route | Purpose |
|---|---|
| `/portfolio/import` | Shared wizard entry — import type selection first |
| `/portfolio/import?type=purchases` | Deep-link directly to purchases wizard (skips type selection) |
| `/portfolio/import?type=dividends` | Deep-link to dividend wizard (existing) |
| `/portfolio/movements?status=pending_reconciliation&year=2026` | Unified reconciliation queue (both types) |
| `/portfolio/movements?status=pending_reconciliation&year=2026&ca_type=SHARE_ACQUISITION` | Queue filtered to purchase-origin pending rows |

### Entry Points

- **Import history** button on the Movements page header → `/portfolio/import` (type selector)
- **Import purchases** quick-link in the Movements page import sub-menu
- Empty-state CTA on `/portfolio/securities`

### Phase Placement

| Phase | Scope |
|---|---|
| **Phase 1 (MVP)** | Manual BUY/SELL/DIVIDEND/DIV+STOCK via slide-over form |
| **Phase 1.5** | Dividend CSV import (8-column), alias mapping, rights reconciliation |
| **Phase 1b (this document)** | Purchase CSV import (7-column), corporate-action-pending classification, possible-top-up suggestion, purchase reconciliation queue, reclassify-as-top-up flow |
| **Phase 2 (deferred)** | Charts, Economics integration, fiscal export, broker-native statement import |

**Phase 1b is independent of Phase 1.5** — the purchase import can ship before or after the dividend import. They share the wizard shell and alias store, but neither blocks the other. The recommended sequence is 1.5 first (most records are dividend rows) then 1b, but either order works.

**Wizard shell is a single shared component.** The type selector added in Phase 1b also retro-fits the dividend import into the same entry point — the dividend import wizard does not need to be rebuilt, only wrapped.

---

## Summary

**Source format:** 7 fixed columns (Año, Empresa, Fecha compra, Valor compra, Acciones, Total (€), Comisión). All amounts EUR. Lossless high-precision decimal parsing and storage; compact 4dp display with full-precision tooltip and expand view.

**Wizard:** same 4-step framework as dividend import, prepended by an import-type selector. `Type → Upload → Metadata → Preview → Confirm`. Source currency is EUR-fixed; no FX input needed. Account optional, blank by default, no warning.

**Row classification (deterministic):**
- `BUY` — price > 0, shares > 0, total > 0
- `🔵 Corporate action pending` — price = total = commission = 0, shares > 0; not classified as BUY; enters reconciliation queue
- Blocking for ambiguous partial-zero combinations

**`💡 Possible top-up` is advisory only** — a chip on BUY rows within 30 days of a zero-cost row for the same company. Never changes classification automatically. Manual linkage via [Link →] in preview or post-import reclassification.

**Manual reclassification:** user can explicitly stage a BUY row as a `CASH_TOP_UP` leg by linking it to a `ca_event` in preview. Post-import, a "Reclassify" action voids the BUY and creates a top-up leg (audit trail preserved).

**One event to many rows:** a single `ca_event` accepts multiple `SHARE_ACQUISITION` and `CASH_TOP_UP` legs from this import. Preview groups them visually with a shared connector. The reconciliation form shows all linked legs.

**Duplicate detection:** account-free dedup keys. BUY: `security|BUY|date|shares|total_eur`. Corp. action: `security|SHARE_ACQ|date|shares`.

**Alias mapping:** shared with dividend import (global, account-agnostic store).

**Reconciliation queue at `/portfolio/movements?status=pending_reconciliation&year=2026`** extended with a type filter. `Corporate action pending` rows appear alongside rights-pending dividend rows. Default year = 2026; older years stored but not shown by default.

**Phase 1b** — ships as an extension of the shared wizard shell. Independent of Phase 1.5 (dividend import), recommended after it. Wizard shell retro-fits the dividend import into the same `/portfolio/import` entry point.
