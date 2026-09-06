# Portfolio Transfer Plan: Broker-to-Broker Custody Transfers

**Date:** 2026-09-06  
**Author:** Danny (Lead, Architecture)  
**Status:** PROPOSED — planning only, no production code  
**Directive:** `.squad/decisions/inbox/copilot-directive-20260906T090638+0200.md`  
**Source design:** `.squad/designs/portfolio-ledger-securities-unified-design.md`  
**Decision ref:** `decisions.md` §1 (MVP Architecture), §Phased Roadmap

---

## Problem Statement

Current custody of a security may differ from the broker where the historical purchase occurred. A user who bought AAPL at Fidelity and later transferred it to Interactive Brokers needs the ledger to reflect:

1. The original BUY remains associated with the Fidelity account (historical provenance preserved).
2. Current holdings show AAPL at Interactive Brokers (custody reality).
3. Total portfolio quantity is unchanged by the transfer.
4. Acquisition date, tax lots, cost basis, average acquisition cost, and realized gain are unchanged.
5. Transfer fees (if any) are explicit cash outflows, never silently absorbed into cost basis.

Without a transfer model, imported data from multiple brokers would produce negative inventory at the source and unexplained positions at the destination — a reconciliation deadlock.

---

## Chosen Model: Paired TRANSFER_OUT / TRANSFER_IN Documents

### Decision

Use **paired atomic `TRANSFER_OUT` / `TRANSFER_IN` documents** linked by a shared `transfer_group_id`, rather than a single parent `TRANSFER` document with two account legs.

### Justification

| Factor | Paired OUT/IN (chosen) | Parent TRANSFER with two legs |
|--------|------------------------|-------------------------------|
| **Partition alignment** | ✅ Each leg lives in its own `/account_id` partition — natural Cosmos layout | ❌ Parent must live in one partition or a synthetic `_transfers` partition; breaks the single-partition query pattern |
| **Holdings derivation** | ✅ `TRANSFER_OUT` subtracts from source; `TRANSFER_IN` adds to destination — identical replay logic to BUY/SELL | ❌ Single doc requires special-case branching in holdings computation (inspect both legs, decide which account) |
| **Void/correction** | ✅ Void one leg → API enforces voiding the paired leg via `transfer_group_id` — follows existing soft-delete pattern | ⚠️ Parent doc void is simpler atomically, but roll-forward/rollback of a single doc affecting two partitions is non-trivial |
| **Cosmos transactional batch** | ⚠️ Two partitions = no single-partition batch — requires application-level two-phase write (acceptable; see §Atomicity) | ✅ Single partition = single batch — but at the cost of partition misalignment |
| **Per-account query** | ✅ Each account's movements list shows only its own transfer leg — no cross-partition joins | ❌ Parent doc visible to one account's query; other account must cross-partition query |
| **Import compatibility** | ✅ Import parsers can emit transfer legs like any other movement type | ⚠️ Import must construct a parent with two legs — more complex parser output |
| **Audit trail** | ✅ Each leg carries its own `created_at`, `import_source`, `broker_ref` — independent provenance per account | ⚠️ Single doc merges provenance from two accounts |

**Verdict:** Paired documents are the safer ledger representation because they align with the existing `/account_id` partition key, preserve per-account query isolation, and extend the existing holdings derivation formula with minimal code change. The atomicity gap (cross-partition writes) is mitigated by the `transfer_group_id` idempotency protocol described below.

---

## Data Model

### New Enum Values

```python
# In models.py — TxnType enum extension
class TxnType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    DIVIDEND = "DIVIDEND"
    TRANSFER_OUT = "TRANSFER_OUT"   # NEW
    TRANSFER_IN = "TRANSFER_IN"     # NEW
```

```typescript
// In portfolio.ts — TxnType extension
export type TxnType = "BUY" | "SELL" | "DIVIDEND" | "TRANSFER_OUT" | "TRANSFER_IN";
```

**Migration/backward compatibility:** `TRANSFER_OUT` and `TRANSFER_IN` are additive enum members. Existing queries filtering `txn_type IN ('BUY', 'SELL', 'DIVIDEND')` continue to work unchanged. Holdings derivation adds two new branches. No existing documents require migration.

### TRANSFER_OUT Document (source account)

```json
{
  "id": "txn_fidelity_2024-03-15_AAPL_TRANSFER_OUT_001",
  "doc_type": "ledger_txn",
  "account_id": "fidelity",
  "txn_type": "TRANSFER_OUT",
  "security_id": "XNYS:AAPL",
  "ticker": "AAPL",
  "company_name": "Apple Inc.",
  "trade_date": "2024-03-15",
  "quantity": "50.000000",

  "transfer": {
    "transfer_group_id": "xfer_fidelity_ibkr_2024-03-15_XNYS:AAPL_001",
    "counterparty_account_id": "ibkr",
    "counterparty_leg_id": "txn_ibkr_2024-03-15_AAPL_TRANSFER_IN_001",
    "direction": "OUT",
    "reason": "broker_transfer",
    "notes": "Full position transfer to IBKR"
  },

  "fees": {
    "total": "75.00",
    "currency": "USD",
    "total_eur": "68.82",
    "breakdown": {
      "transfer_fee": "75.00"
    }
  },
  "fx": {
    "rate": "0.917600000",
    "rate_source": "ECB"
  },

  "gross": null,
  "net": null,
  "withholding": null,

  "import_source": "manual",
  "status": "active",
  "created_at": "2026-09-06T07:00:00Z",
  "updated_at": "2026-09-06T07:00:00Z"
}
```

### TRANSFER_IN Document (destination account)

```json
{
  "id": "txn_ibkr_2024-03-15_AAPL_TRANSFER_IN_001",
  "doc_type": "ledger_txn",
  "account_id": "ibkr",
  "txn_type": "TRANSFER_IN",
  "security_id": "XNYS:AAPL",
  "ticker": "AAPL",
  "company_name": "Apple Inc.",
  "trade_date": "2024-03-15",
  "quantity": "50.000000",

  "transfer": {
    "transfer_group_id": "xfer_fidelity_ibkr_2024-03-15_XNYS:AAPL_001",
    "counterparty_account_id": "fidelity",
    "counterparty_leg_id": "txn_fidelity_2024-03-15_AAPL_TRANSFER_OUT_001",
    "direction": "IN",
    "reason": "broker_transfer",
    "notes": "Full position transfer from Fidelity"
  },

  "fees": {
    "total": "0",
    "currency": "USD",
    "total_eur": "0",
    "breakdown": {}
  },
  "fx": {
    "rate": "0.917600000",
    "rate_source": "ECB"
  },

  "gross": null,
  "net": null,
  "withholding": null,

  "import_source": "manual",
  "status": "active",
  "created_at": "2026-09-06T07:00:00Z",
  "updated_at": "2026-09-06T07:00:00Z"
}
```

### Key Schema Decisions

| Field | Rule |
|-------|------|
| `quantity` | Always positive; direction is in `txn_type` (OUT decrements, IN increments) — consistent with I2 |
| `security_id` | **Must be identical** on both legs — API rejects mismatched security_id |
| `account_id` | Source ≠ Destination — API rejects self-transfers |
| `trade_date` | Same on both legs — represents the effective transfer date |
| `gross` / `net` | `null` — transfers are not buy/sell events; no gross/net amounts |
| `withholding` | `null` — transfers are not income events; no withholding |
| `fees` | Optional on either leg; typically on TRANSFER_OUT (broker charges outbound fee). **Fees are pure cash outflow — they never modify cost basis.** |
| `transfer.transfer_group_id` | Deterministic: `xfer_{source_account}_{dest_account}_{date}_{security_id}_{seq}` — idempotency key |
| `transfer.counterparty_account_id` | The other side's account — enables cross-referencing without cross-partition query |
| `transfer.counterparty_leg_id` | Direct pointer to paired document for audit linkage |
| `transfer.reason` | Enum: `broker_transfer`, `account_restructure`, `in_kind_transfer`, `other` |

---

## Invariants

### Existing Invariants — Preserved

| # | Invariant | Impact on Transfers |
|---|-----------|---------------------|
| I1 | `txn_type ∈ {BUY, SELL, DIVIDEND, ...}` | Extended: add `TRANSFER_OUT`, `TRANSFER_IN` |
| I2 | `quantity > 0` for all types; direction in txn_type | ✅ Unchanged — TRANSFER_OUT subtracts, TRANSFER_IN adds |
| I3 | Money fields: amount + currency + eur_amount | ✅ Fees follow this pattern; gross/net/withholding are null |
| I4 | Withholding null ≠ zero | N/A — withholding is null for transfers |
| I5 | Derived holdings never negative | ✅ **Extended** — see §Negative Source Inventory |
| I6 | Movements immutable | ✅ Unchanged — transfers follow same append-only pattern |
| I7 | `net = gross - fees - wht` | N/A — transfers have no gross/net computation |
| I8 | `deleted_at` excluded from aggregates | ✅ Unchanged |

### New Invariants — Transfer-Specific

| # | Invariant | Enforced by |
|---|-----------|-------------|
| T1 | Every `TRANSFER_OUT` must have exactly one `TRANSFER_IN` with matching `transfer_group_id`, and vice versa | API write + reconciliation check |
| T2 | `security_id` identical on both legs | API validation at creation |
| T3 | `quantity` identical on both legs | API validation at creation |
| T4 | `source_account_id ≠ destination_account_id` | API validation at creation |
| T5 | Transfer fees do not alter cost basis or average acquisition cost | Holdings derivation — transfer fees excluded from cost basis aggregation |
| T6 | Total portfolio quantity unchanged: `SUM(all_account_shares)` before = after | Implicit from T3: TRANSFER_OUT subtracts X, TRANSFER_IN adds X |
| T7 | Acquisition date unchanged — the original BUY's `trade_date` is unaffected | By design: transfers do not modify existing BUY documents |
| T8 | Realized gain unchanged — transfers are not taxable events | By design: no gross/net on transfers |
| T9 | Paired void: voiding either leg voids both (via `transfer_group_id` lookup) | API void handler |
| T10 | Orphan detection: a TRANSFER_OUT without matching TRANSFER_IN (or vice versa) is flagged as `ORPHAN_TRANSFER` warning | Reconciliation service (Phase 2a health check) |

---

## Holdings Derivation — Extended Formula

### Current Formula (unchanged for BUY/SELL/DIVIDEND)

```
total_shares(account, security) =
    SUM(BUY.quantity where account)
  - SUM(SELL.quantity where account)
  + SUM(DIVIDEND.share_leg.quantity where account)
```

### Extended with Transfers

```
total_shares(account, security) =
    SUM(BUY.quantity where account)
  - SUM(SELL.quantity where account)
  + SUM(DIVIDEND.share_leg.quantity where account)
  + SUM(TRANSFER_IN.quantity where account)
  - SUM(TRANSFER_OUT.quantity where account)
```

### Portfolio-Wide Total (unchanged by transfer)

```
total_shares(security) =
    SUM over all accounts [ total_shares(account, security) ]
```

Since TRANSFER_OUT subtracts X from source and TRANSFER_IN adds X to destination, portfolio-wide total is invariant.

### Average Cost Basis — Unchanged

Transfers do **not** affect average cost basis computation. Cost basis is derived exclusively from BUY movements (and zero-cost corporate actions with INCOMPLETE status). The `TRANSFER_IN` leg inherits the cost basis of the original BUY(s) implicitly — because cost basis is computed portfolio-wide from all BUY movements, not per-account. Transfer fees are excluded from cost computation.

When holdings are filtered per-account (e.g., "what is my average cost at IBKR?"), the per-account cost basis may not be directly computable without tracing the original BUY(s). This is a known limitation deferred to Phase 3 (per-account cost basis tracking, FIFO/LIFO lots).

---

## Negative Source Inventory Policy

### Rule

Before committing a `TRANSFER_OUT`, the API checks:

```
current_shares(source_account, security) >= transfer_quantity
```

If this check fails:

- **Manual entry:** API returns HTTP 422 with `{"error": "insufficient_inventory", "detail": "Source account 'fidelity' holds 30 shares of XNYS:AAPL but transfer requests 50"}`. User must import/enter the missing BUY movements first.
- **Import (Phase 2a):** The transfer is staged with a `NEGATIVE_INVENTORY` warning (non-blocking, consistent with existing warning I5 behavior for sells that precede unimported buys). The preview shows the warning; user may confirm acknowledging incomplete import history.

### Reconciliation path

Negative inventory after transfer is a temporary data-quality signal, not a permanent error. Once the user imports earlier BUY movements, the warning auto-resolves on next holdings recomputation.

---

## Atomicity — Cross-Partition Write Protocol

### Problem

TRANSFER_OUT and TRANSFER_IN live in different `/account_id` partitions. Cosmos transactional batch is single-partition only. We cannot atomically write both legs in one operation.

### Protocol: Application-Level Two-Phase Write with Idempotency

```
1. Generate deterministic transfer_group_id
2. Write TRANSFER_OUT to source partition
   - On success → continue
   - On conflict (409, same transfer_group_id) → idempotent skip
3. Write TRANSFER_IN to destination partition
   - On success → transfer complete
   - On conflict (409, same transfer_group_id) → idempotent skip
   - On failure → TRANSFER_OUT is orphaned → reconciliation detects via T10
4. Return success only if both writes confirmed
```

### Failure Recovery

| Failure Point | State | Recovery |
|---------------|-------|----------|
| After step 2, before step 3 | TRANSFER_OUT exists, TRANSFER_IN missing | Orphan detection (T10) flags in next reconciliation run; manual retry via API creates TRANSFER_IN (idempotent on `transfer_group_id`) |
| Step 2 fails | Nothing written | Clean failure; user retries |
| Step 3 conflict (retry) | Both exist | Idempotent; no action |
| Both succeed | Complete | Normal path |

### Idempotency

The `transfer_group_id` is the idempotency key. It is deterministic (derived from source account, destination account, date, security_id, and sequence number). Duplicate writes with the same `transfer_group_id` are rejected (409 Conflict) or silently succeed (upsert idempotency, depending on implementation choice). The API never creates duplicate transfer legs.

---

## Void / Correction Behavior

### Void

Voiding a transfer follows existing soft-delete pattern with paired enforcement:

```
PATCH /api/portfolio/movements/{movement_id}/void

1. Read target movement
2. If txn_type IN (TRANSFER_OUT, TRANSFER_IN):
   a. Read paired leg via transfer.counterparty_leg_id
   b. Soft-delete BOTH legs (set deleted_at on both)
   c. If paired leg already voided, only void the target
   d. If paired leg not found (orphan), void target + log warning
3. Return both voided documents in response
```

**Partial void is not allowed.** Voiding one transfer leg always voids the pair. This preserves T6 (portfolio quantity invariant).

### Correction

Corrections follow the existing correction chain pattern: void the erroneous pair, then create a new transfer pair with `replaces_id` pointing to the voided leg. The `correction_chain` field links the history.

---

## API Surface — Transfer Endpoints

### Create Transfer (new endpoint)

```
POST /api/portfolio/transfers
```

**Request body:**
```json
{
  "source_account_id": "fidelity",
  "destination_account_id": "ibkr",
  "security_id": "XNYS:AAPL",
  "trade_date": "2024-03-15",
  "quantity": "50.000000",
  "fees": {
    "source": { "total": "75.00", "currency": "USD" },
    "destination": { "total": "0", "currency": "USD" }
  },
  "fx": { "rate": "0.917600000", "rate_source": "ECB" },
  "reason": "broker_transfer",
  "notes": "Full position transfer to IBKR"
}
```

**Response (201):**
```json
{
  "transfer_group_id": "xfer_fidelity_ibkr_2024-03-15_XNYS:AAPL_001",
  "transfer_out": { "id": "txn_fidelity_...", "account_id": "fidelity", ... },
  "transfer_in": { "id": "txn_ibkr_...", "account_id": "ibkr", ... }
}
```

**Validation (422):**
- `source_account_id == destination_account_id` → "Source and destination accounts must be different"
- `security_id` not found in security master → "Unknown security"
- `quantity <= 0` → "Quantity must be positive"
- Insufficient source inventory (manual mode) → "insufficient_inventory"

### Existing Endpoints — Backward Compatible

| Endpoint | Change |
|----------|--------|
| `GET /api/portfolio/movements` | Returns TRANSFER_OUT/TRANSFER_IN alongside BUY/SELL/DIVIDEND; filter by `txn_type` works |
| `GET /api/portfolio/securities` (holdings) | Holdings computation extended per §Holdings Derivation |
| `PATCH /api/portfolio/movements/:id/void` | Extended for paired void per §Void |
| All other endpoints | **No change** |

---

## Import & Reconciliation Compatibility

### Manual Web Flow

A dedicated "Record Transfer" form in the Movements or Holdings page:
1. User selects source account, destination account, security, date, quantity.
2. Optional: transfer fee on either side.
3. Form calls `POST /api/portfolio/transfers`.
4. Both legs appear in the Movements list immediately.

### CSV Import (Phase 2a)

Transfer rows in imported CSVs are recognized by a `TRANSFER` or `TRASPASO` type column. The import parser:
1. Detects transfer-type rows.
2. Groups matching OUT/IN pairs by security, date, and quantity.
3. Unpaired transfer rows surface as `ORPHAN_TRANSFER` warnings in preview.
4. On commit, paired rows call the transfer write protocol.

### Broker Reconciliation (Phase 2b)

The reconciliation tool compares imported broker statements against ledger state. Transfers are critical for reconciliation because:
- Source broker statement shows position departed on date X.
- Destination broker statement shows position arrived on date X (or X+T settlement).
- Without transfer model, reconciliation sees "missing shares at source" + "unexplained shares at destination" — a false mismatch.
- With transfer model, reconciliation matches: `TRANSFER_OUT(source, date, qty)` ↔ `TRANSFER_IN(dest, date, qty)` — confirmed correct.

**This is why transfers must precede reconciliation in the roadmap.**

---

## Roadmap Placement

### Current Roadmap

| Phase | Scope |
|-------|-------|
| 1 (MVP) | Manual entry, read-only holdings, CSV import chat |
| 2 | Excel import, reconciliation tool, auto-FX |
| 3 | Ledger-derived total_shares, FIFO/LIFO, snapshots |
| 4 | Fiscal export, tax reporting |
| 5 | Charts, analytics, time-series |

### Amended Roadmap

| Phase | Scope |
|-------|-------|
| **1 (MVP)** | Manual entry, read-only holdings, CSV import chat — **unchanged** |
| **1b** | Manual movement entry forms (BUY/SELL/DIVIDEND) — as already planned |
| **2a (NEW)** | **Transfer model + manual transfer form** — paired TRANSFER_OUT/TRANSFER_IN, transfer API, transfer form in web UI, holdings derivation update, orphan detection |
| **2b** | Excel/CSV import (parser, batch, dedup) — import recognizes transfer rows, groups pairs |
| **2c** | Broker reconciliation tool — depends on transfer model for cross-broker position matching |
| **2d** | Auto-FX fetching (ECB) — independent, can parallel with 2b/2c |
| **3** | Ledger-derived total_shares, FIFO/LIFO, **per-account cost basis** (traces original BUYs through transfers), snapshots |
| **4** | Fiscal export, tax reporting |
| **5** | Charts, analytics, time-series |

### Why Transfers Precede Reconciliation (Phase 2a before 2c)

1. **Import data quality:** Historical broker CSVs will contain transfers. Without the model, these rows are either dropped (data loss) or misclassified as BUY/SELL (incorrect cost basis and double-counted shares).
2. **Reconciliation correctness:** The reconciliation tool must understand that shares departed one account and arrived at another. Without transfer semantics, every cross-broker position move appears as an unexplained inventory discrepancy.
3. **Manual entry unblock:** Users may want to record transfers before bulk import is ready — the manual transfer form in Phase 2a enables this immediately.
4. **Minimal dependency:** The transfer model depends only on the existing ledger infrastructure (Phase 1). It does not require import parsing (Phase 2b) or reconciliation (Phase 2c).

---

## Migration & Backward Compatibility

### Enum Extension

`TRANSFER_OUT` and `TRANSFER_IN` are new values appended to `TxnType`. No existing value is renamed or removed. Code that switches on `txn_type` must handle unknown types gracefully (existing pattern: unknown types are ignored in holdings aggregation).

### Existing Data

No existing documents require migration. All existing BUY/SELL/DIVIDEND movements remain unchanged. The `transfer` sub-object is only present on new transfer documents.

### API Versioning

No breaking API changes. The transfer endpoint is additive (`POST /api/portfolio/transfers`). Existing endpoints return transfer documents alongside other movements — clients that don't understand TRANSFER_OUT/TRANSFER_IN simply display them as movements with an unfamiliar type (graceful degradation).

### Frontend

TypeScript `TxnType` union gains two members. Components that render movement type badges add cases for TRANSFER_OUT (→ arrow icon) and TRANSFER_IN (← arrow icon). Holdings computation in any frontend aggregation (if any) mirrors the backend formula extension.

---

## Open Questions

1. **Partial transfers:** Should the system support transferring a subset of shares (e.g., 30 of 50 held)? **Tentative answer: yes** — the model supports any positive quantity up to available inventory.
2. **Settlement lag:** Should `trade_date` vs. `settlement_date` differ between legs (e.g., T+2 at source, T+3 at destination)? **Tentative answer: both legs carry same `trade_date`; settlement_date optional and may differ per leg.**
3. **In-kind transfers with fractional shares:** Some brokers liquidate fractional shares during transfer. **Tentative answer: model as SELL of fractional shares + TRANSFER of whole shares — two separate events.**
4. **Transfer-in without known source:** User may record a transfer-in from an untracked external account. **Tentative answer: allow `source_account_id = "_external"` as a sentinel; the TRANSFER_OUT leg lives in `_external` partition as a bookkeeping record.**

---

## Summary

| Aspect | Decision |
|--------|----------|
| **Model** | Paired `TRANSFER_OUT` / `TRANSFER_IN` linked by `transfer_group_id` |
| **Partition** | Each leg in its own `/account_id` partition |
| **Atomicity** | Application-level two-phase write with idempotent `transfer_group_id` |
| **Cost basis** | Unchanged — transfers excluded from cost basis computation |
| **Fees** | Cash outflow on either leg; never affects cost basis |
| **Holdings** | Extended formula: `+TRANSFER_IN`, `-TRANSFER_OUT` |
| **Void** | Paired void — both legs voided together |
| **Roadmap** | **Phase 2a** — after MVP, before import and reconciliation |
| **Backward compat** | Additive enum, additive endpoint, no migration |
