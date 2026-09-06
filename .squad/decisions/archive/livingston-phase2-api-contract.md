# Portfolio Phase 2 — Backend API Contract

**Author:** Livingston  
**Date:** 2026-09-06  
**Status:** IMPLEMENTED — ready for Rusty to consume  
**Backend base path:** `/api`

---

## New Enums / Document Conventions

### Extended `txn_type` values
`BUY`, `SELL`, `DIVIDEND` (unchanged) + `TRANSFER_OUT`, `TRANSFER_IN` (new)

### `correction_status` on ledger movements
`ACTIVE` (default if absent), `SUPERSEDED` (replaced/reassigned), `VOIDED` (explicitly voided)  
Holdings computation **excludes** SUPERSEDED and VOIDED records.

### Broker slugs (AccountBroker)
`fidelity`, `heytrade`, `ing`, `interactive_brokers`, `other`

---

## Endpoints

### Accounts

#### `GET /api/portfolio/accounts`
List all broker accounts.
```json
// Response 200
{
  "accounts": [
    {
      "id": "acct_ibkr_main",
      "account_id": "acct_ibkr_main",
      "broker": "interactive_brokers",
      "name": "Main IBKR",
      "currency": "USD",
      "description": "...",
      "created_at": "...",
      "updated_at": "..."
    }
  ]
}
```

#### `POST /api/portfolio/accounts`
Create a broker account.
```json
// Request body
{
  "broker": "interactive_brokers",          // required
  "name": "Main IBKR",                       // required
  "currency": "USD",                         // optional, default "EUR"
  "description": "Optional description"     // optional
}
// Response 201: account doc
// Response 400: { "error": "validation_error", "detail": "..." }
// Response 409: { "error": "conflict", "detail": "Account already exists" }
```

#### `GET /api/portfolio/accounts/{account_id}`
Get a single account.
```json
// Response 200: account doc
// Response 404: { "error": "not_found", "detail": "..." }
```

#### `DELETE /api/portfolio/accounts/{account_id}`
Delete an account. Blocked if the account has active (non-deleted, non-superseded) movements.
```json
// Response 200: { "id": "...", "deleted_at": "..." }
// Response 404: { "error": "not_found", "detail": "..." }
// Response 409: { "error": "account_has_movements", "detail": "...", "movement_count": 12 }
```

---

### Manual Movement Creation

#### `POST /api/portfolio/movements`
Create a manual BUY, SELL, DIVIDEND, or TRANSFER.

**BUY / SELL / DIVIDEND body:**
```json
{
  "txn_type": "BUY",                       // BUY | SELL | DIVIDEND
  "security_id": "XNYS:AAPL",
  "trade_date": "2026-01-15",
  "account_id": "_unassigned",             // optional, defaults to _unassigned
  "quantity": "100",                       // required for BUY/SELL; 0 OK for DIVIDEND
  "gross": {
    "amount": "18250.00",
    "currency": "EUR",
    "eur_amount": "18250.00"
  },
  "fees": {                                // optional
    "total": "7.50",
    "currency": "EUR",
    "total_eur": "7.50"
  },
  "withholding": null,                     // optional WithholdingInfo
  "fx": { "rate": "1.000000000", "rate_source": "ECB" }, // optional
  "sales_type": "ACCIONES",               // SELL only: ACCIONES | DERECHOS (default ACCIONES)
  "cost_basis_status": "COMPLETE",        // optional BUY: COMPLETE | INCOMPLETE
  "notes": "optional free text"
}
// Response 201: created ledger_txn doc
// Response 400: validation_error
// Response 409: { "error": "probable_duplicate", "existing": { ... } }  — if matching movement found
```

**TRANSFER body** — use `POST /api/portfolio/transfers` instead (see below).

---

### Movement Detail & Correction

#### `GET /api/portfolio/movements/{movement_id}?account_id=xxx`
Get a single movement with its full correction chain.
```json
// Response 200
{
  "movement": { ... },           // the movement doc
  "superseded_by": { ... }      // if this has been replaced; else null
}
// Response 404: not_found
```

#### `POST /api/portfolio/movements/{movement_id}/correct`
Replace/correct a movement. Creates a new "active" doc, marks original SUPERSEDED.
```json
// Request body (same shape as movement create, minus txn_type and security_id — inherited)
{
  "account_id": "...",             // required: must match original partition
  "correction_note": "Price was wrong",  // required reason
  // ... corrected fields (any movement fields to override)
  "trade_date": "2026-01-15",
  "quantity": "100",
  "gross": { "amount": "18000.00", "currency": "EUR", "eur_amount": "18000.00" }
}
// Response 200
{
  "original": { ...original doc with correction_status: "SUPERSEDED" },
  "replacement": { ...new doc with corrects_movement_id set }
}
// Response 404: not_found
// Response 409: { "error": "already_superseded", "detail": "..." }
```

---

### Transfers

#### `POST /api/portfolio/transfers`
Create a paired TRANSFER_OUT (source) + TRANSFER_IN (destination) in one call.
```json
// Request body
{
  "security_id": "XNYS:AAPL",
  "trade_date": "2026-01-15",
  "quantity": "50",
  "source_account_id": "acct_ibkr_main",
  "dest_account_id": "acct_fidelity_main",
  "cost_basis_override_eur": null,       // null = auto-derive from source holdings
  "transfer_fee": {                      // optional
    "amount": "10.00",
    "currency": "EUR",
    "eur_amount": "10.00"
  },
  "notes": "..."
}
// Response 201
{
  "transfer_out": { ...ledger_txn with txn_type TRANSFER_OUT },
  "transfer_in":  { ...ledger_txn with txn_type TRANSFER_IN },
  "transfer_group_id": "trf_..."
}
// Response 400: validation_error (same account, missing fields)
// Response 409: { "error": "insufficient_shares", "detail": "...", "available": "40", "requested": "50" }
```

Shared fields on both legs:
- `transfer_group_id`: same UUID string prefixed `trf_`
- `transfer_peer_id`: ID of the other leg
- `transfer_source_account_id` + `transfer_dest_account_id`
- `transfer_cost_basis_derived_eur`: auto-computed avg cost × qty from source holdings at date
- `transfer_cost_basis_eur`: effective cost (equals derived unless overridden)
- `transfer_cost_basis_overridden`: boolean

---

### Movement Reassignment

#### `POST /api/portfolio/movements/{movement_id}/reassign`
Move a single historical movement to a different account.
```json
// Request body
{
  "source_account_id": "...",         // required: current account (partition key)
  "dest_account_id": "...",           // required: destination
  "reason": "Imported to wrong account"
}
// Response 200
{
  "original_id": "...",
  "new_id": "...",
  "dest_account_id": "..."
}
// Response 404: not_found
// Response 409: same_account | already_reassigned
```

#### `POST /api/portfolio/movements/batch-reassign/preview`
**Dry-run preview** — uses the exact same selection predicate as execution. Read-only; no writes.

The client **must not** pass the returned count back to execution. The server always re-derives the candidate set at execution time.
```json
// Request body (same shape as batch-reassign)
{
  "source_account_id": "...",        // required
  "dest_account_id": "...",          // required
  "security_id": "XNYS:AAPL",       // optional filter
  "date_from": "2026-01-01",         // optional filter
  "date_to": "2026-06-30"            // optional filter
}
// Response 200
{
  "affected_count": 12,
  "movement_ids": ["mvt_...", ...],   // all matching IDs (server-computed)
  "sample": [                          // first 10 items (bounded)
    {
      "id": "mvt_...",
      "security_id": "XNYS:AAPL",
      "txn_type": "BUY",
      "trade_date": "2026-01-15",
      "quantity": "100",
      "account_id": "_unassigned"
    }
  ],
  "source_account_id": "...",
  "dest_account_id": "..."
}
// Response 400: validation_error (same account, missing required fields)
```

#### `POST /api/portfolio/movements/batch-reassign`
Reassign all matching movements between accounts.
```json
// Request body
{
  "source_account_id": "...",        // required
  "dest_account_id": "...",          // required
  "security_id": "XNYS:AAPL",       // optional filter
  "date_from": "2026-01-01",         // optional filter
  "date_to": "2026-06-30",           // optional filter
  "reason": "Bulk correction"
}
// Response 200
{
  "reassigned_count": 12,
  "skipped_count": 0,
  "ids": ["new_id_1", ...]
}
// Response 400: validation_error (same account)
```

---

### FX Rates

#### `GET /api/fx/rates?from_currency=USD&to_currency=EUR&date=2026-01-15`
Look up FX rate (ECB source).
```json
// Response 200
{
  "from_currency": "USD",
  "to_currency": "EUR",
  "date": "2026-01-15",
  "rate": "0.921500000",           // EUR per 1 unit of from_currency
  "rate_source": "ECB",
  "note": null
}
// Response 400: validation_error (unsupported currency)
// Response 404: { "error": "rate_not_found", "detail": "No ECB rate for USD on 2026-01-15" }
// Response 503: { "error": "fx_unavailable", "detail": "ECB API unreachable" }
```

Notes:
- `from_currency = EUR` always returns 1.0
- Rate formula: `eur_amount = txn_amount × rate`
- Only EUR as `to_currency` is supported (Phase 2)

---

### Holdings (extended)

#### `GET /api/portfolio/holdings?account_id=...`
Unchanged signature. Now excludes SUPERSEDED and VOIDED movements.  
TRANSFER_OUT subtracts shares from the account; TRANSFER_IN adds shares to the account.  
Global (no filter): TRANSFER_OUT and TRANSFER_IN net to zero globally.

---

## Ledger Movement Shape (extended)

```json
{
  "id": "mvt_...",
  "doc_type": "ledger_txn",
  "txn_type": "BUY | SELL | DIVIDEND | TRANSFER_OUT | TRANSFER_IN",
  "security_id": "MIC:TICKER",
  "ticker": "AAPL",
  "trade_date": "2026-01-15",
  "account_id": "acct_ibkr_main",
  "quantity": "100",
  "gross": { "amount": "18250.00", "currency": "EUR", "eur_amount": "18250.00" },
  "fees": { "total": "7.50", "currency": "EUR", "total_eur": "7.50" },
  "net": { "amount": "18242.50", "currency": "EUR", "eur_amount": "18242.50" },
  "fx": { "rate": "1.000000000", "rate_source": "ECB" },
  "import_source": "manual",
  "created_at": "...",
  
  // Correction fields (optional):
  "correction_status": "ACTIVE",        // omitted on old docs = ACTIVE
  "corrects_movement_id": "mvt_...",    // if this replaces another
  "superseded_by": "mvt_...",           // if this was replaced
  "correction_note": "reason",
  
  // Transfer fields (TRANSFER_* only):
  "transfer_group_id": "trf_...",
  "transfer_peer_id": "mvt_...",
  "transfer_source_account_id": "...",
  "transfer_dest_account_id": "...",
  "transfer_cost_basis_derived_eur": "...",
  "transfer_cost_basis_eur": "...",
  "transfer_cost_basis_overridden": false,
  "transfer_fee": { "amount": "10.00", "currency": "EUR", "eur_amount": "10.00" },
  
  // Reassignment audit fields (optional):
  "reassigned_from": { "account_id": "...", "movement_id": "..." },
  
  // SELL only:
  "sales_type": "ACCIONES | DERECHOS"
}
```
