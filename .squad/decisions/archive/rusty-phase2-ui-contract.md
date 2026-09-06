# Rusty Phase 2 UI Contract — Assumed API Shapes

**Date:** 2026-09-06
**Author:** Rusty (Frontend Agent)
**Status:** ~~PROVISIONAL~~ → **SUPERSEDED** — Reconciliation pass applied 2026-09-06  
**Ref:** `copilot-directive-20260906-phase2-portfolio.md`  
**See:** `livingston-phase2-api-contract.md` (authoritative), `rusty history.md` Phase 2 Reconciliation section

---

> **This file is no longer the active contract.**  
> All frontend types and API calls now match `livingston-phase2-api-contract.md`.  
> Reconciliation pass applied. See Rusty history.md for the full diff and gap flag (batch reassignment preview).

---

## Assumed Phase 2 API Shapes

### Accounts

#### `GET /api/portfolio/accounts`
```json
{ "accounts": [BrokerAccount, ...] }
```

#### `POST /api/portfolio/accounts`
Request: `{ broker_type, name, account_number?, currency?, notes? }`
Response 201: `BrokerAccount`

#### `PUT /api/portfolio/accounts/{account_id}`
Request: Same fields, all optional
Response 200: Updated `BrokerAccount`

#### `DELETE /api/portfolio/accounts/{account_id}`
Response 200: `{}`
Response 409: `{ "error": "has_movements", "detail": "Cannot delete account with associated movements" }` — surfaced clearly in UI

### Manual Movement Entry

#### `POST /api/portfolio/movements`
All types use one endpoint with `txn_type` field. Type-specific fields included per type:

```json
{
  "txn_type": "BUY" | "SELL" | "DIVIDEND" | "TRANSFER",
  "security_id": "XMAD:SAN",
  "account_id": "_unassigned",
  "trade_date": "YYYY-MM-DD",
  "notes": "...",

  // BUY/SELL
  "quantity": "100",
  "price_per_share": "5.50",
  "total_cost": "550.00",        // BUY
  "total_proceeds": "600.00",    // SELL
  "currency": "EUR",
  "fees": "2.00",
  "sales_type": "ACCIONES",      // SELL only

  // DIVIDEND
  "gross_amount": "86.25",
  "withholding_source": { "country": "US", "rate_pct": "15.00", "amount_eur": "12.94" },
  "withholding_dest": null,

  // TRANSFER
  "source_account_id": "acc_xxx",
  "dest_account_id": "acc_yyy",
  "carried_cost_basis": "4.80",   // null = backend derives from source account holdings
  "transfer_fees": "5.00"          // stored separately, not added to cost basis
}
```

Response 201: `LedgerMovement`
Response 409: `{ "error": "insufficient_shares", "detail": "..." }` for TRANSFER with source shares < quantity

### Movement Correction

#### `POST /api/portfolio/movements/{id}/correct`
```json
{
  "correction_reason": "Wrong trade date",
  "corrections": {
    "trade_date": "2024-06-01",
    "quantity": "150"
  }
}
```
Response 200:
```json
{ "original_id": "txn_...", "corrected_id": "txn_...", "corrected_at": "..." }
```

### Account Reassignment

#### `POST /api/portfolio/movements/{id}/reassign`
```json
{ "new_account_id": "acc_xxx", "reason": "..." }
```
Response 200:
```json
{ "movement_id": "...", "old_account_id": "...", "new_account_id": "..." }
```

#### `GET /api/portfolio/movements/reassign/preview`
Query: `security_id`, `new_account_id`, `date_from?`, `date_to?`, `current_account_id?`
Response 200:
```json
{ "affected_count": 12, "security_id": "...", "new_account_id": "...", "sample_movement_ids": [...] }
```

#### `POST /api/portfolio/movements/reassign/batch`
```json
{
  "security_id": "XMAD:SAN",
  "new_account_id": "acc_xxx",
  "date_from": "2024-01-01",
  "date_to": "2024-12-31",
  "current_account_id": "_unassigned",
  "reason": "Initial account assignment"
}
```
Response 200:
```json
{ "reassigned_count": 12 }
```

### FX Rate Helper

#### `GET /api/portfolio/fx-rate?from=USD&to=EUR&date=2024-06-15`
Response 200:
```json
{ "from_currency": "USD", "to_currency": "EUR", "date": "2024-06-15", "rate": "0.9250", "rate_source": "ECB" }
```

---

## BrokerAccount Shape

```json
{
  "account_id": "acc_<uuid>",
  "broker_type": "FIDELITY" | "HEYTRADE" | "ING" | "INTERACTIVE_BROKERS" | "OTHER",
  "name": "My ING account",
  "account_number": "ES0000000000",
  "currency": "EUR",
  "notes": "...",
  "created_at": "...",
  "updated_at": "..."
}
```

---

## TRANSFER TxnType Extension

Phase 2 extends `TxnType` from `"BUY" | "SELL" | "DIVIDEND"` to also include `"TRANSFER"`. The `LedgerMovement` type already allows unknown txn types in the badge map (falls back to `bg-bg-hover text-text-muted`). No breaking change to existing Phase 1 code.

---

## Notes for Livingston

1. The frontend uses `DELETE /api/portfolio/accounts/{id}` — please return 409 with `error: "has_movements"` when movements exist.
2. TRANSFER movements expect backend to block when source account has insufficient shares and return `error: "insufficient_shares"` with HTTP 409.
3. The correction endpoint creates a replacement and preserves the original — please mark original with `superseded_by` field for future UI filtering.
4. `GET /api/portfolio/holdings` already supports `?account_id=` param (Phase 1) — Phase 2 reuses this.
5. `GET /api/portfolio/movements` already supports `?account_id=` param (Phase 1) — Phase 2 reuses this.
6. New movement creation (`POST /api/portfolio/movements`) must set `import_source: "manual"` on the ledger record.
