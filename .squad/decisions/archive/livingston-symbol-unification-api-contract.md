# Livingston → Rusty: Symbol Unification rev 3 — Backend API Contract

_Authored: Livingston (Persistence & Integration Engineer)_
_For: Rusty (Frontend)_
_Updated: 2026-09-06 — reconciled with Rusty's UI contract_
_Status: FINAL — all endpoints implemented, 113/113 tests pass, shapes verified_

---

## Overview

All new endpoints are served from the existing FastAPI backend. Base URL: `/` (same origin). All responses are JSON.

---

## 1. GET /api/symbols/overview — Watchlist Overview

Returns two mutually exclusive sections plus backward-compat `rows`.

**Response shape:**
```json
{
  "rows": [...],                // legacy flat union (portfolio first, then watchlist)
  "portfolio_rows": [
    {
      "symbol": "AAPL",
      "display_name": "",
      "list_section": "portfolio",
      "security_id": "XNYS:AAPL",
      "portfolio_shares": "150.000000",
      "portfolio_avg_cost_eur": null,
      "portfolio_invested_eur": null,
      "category": "", "dgi_score": null, "tech_timing": null,
      "entry_tag": "", "momentum": "",
      "price": null, "total_shares": 150,
      "active_count": 0, "in_calls": 0, "put_exposure": 0, "call_exposure": 0,
      "watchlist": { "covered_call": true, "cash_secured_put": false, "buy_tracker": false }
    }
  ],
  "watchlist_rows": [
    {
      "symbol": "MSFT",
      "list_section": "watchlist",
      "security_id": "XNAS:MSFT",
      "portfolio_shares": null,
      "portfolio_avg_cost_eur": null,
      "portfolio_invested_eur": null
    }
  ],
  "portfolio_count": 3,
  "watchlist_count": 2,
  "symbol_count": 5,
  "total_call_exposure": 0.0,
  "total_put_exposure": 0.0,
  "last_update_ts": ""
}
```

**Rules:**
- Portfolio membership = ANY qualifying ledger entry including deleted/superseded/zero-share
- Rows are mutually exclusive (a symbol cannot appear in both lists)
- `rows` = `portfolio_rows` + `watchlist_rows` (same content, backward compat)
- `portfolio_avg_cost_eur` and `portfolio_invested_eur` may be null if holdings data is unavailable

---

## 2. GET /api/symbols/{symbol}/detail — Unified Symbol Detail

Accepts both `MIC:TICKER` canonical and legacy bare `TICKER`.

**200 response:**
```json
{
  "symbol": "AAPL",
  "display_name": "",
  "exchange": "XNYS",
  "total_shares": 150,
  "symbol_state": "watchlist_and_portfolio",
  "security_id": "XNYS:AAPL",
  "security": {
    "security_id": "XNYS:AAPL",
    "company_name": "Apple Inc.",
    "exchange_mic": "XNYS",
    "isin": "US0378331005",
    "listing_currency": "USD",
    "status": "ACTIVE"
  },
  "portfolio": {
    "current_shares": "150.000000",
    "average_cost_eur": "142.35",
    "current_invested_eur": "21352.50",
    "total_dividends_eur": "320.40",
    "holdings_by_account": [
      {
        "account_id": "degiro",
        "shares": "100.000000",
        "avg_cost_eur": "142.35"
      }
    ],
    "recent_movements": [
      {
        "id": "txn_aapl_buy",
        "txn_type": "BUY",
        "trade_date": "2026-08-15",
        "quantity": "100",
        "gross_eur": "14235.00"
      }
    ],
    "movement_count": 1
  },
  "watchlist": { "covered_call": true, "cash_secured_put": false, "buy_tracker": false },
  "telegram_notifications_enabled": false,
  "enrichment": {},
  "positions": [],
  "activities": [],
  "plans": [],
  "summary": { "in_calls": 0, "put_exposure": 0, "call_exposure": 0, "active_count": 0 },
  "next_earnings_date": null,
  "is_paused": false
}
```

**`symbol_state` values (complete enum):**
- `"watchlist_only"` — has symbol_config, no ledger history
- `"watchlist_and_portfolio"` — has symbol_config + active shares (current_shares > 0)
- `"portfolio_historical"` — has symbol_config + ledger but all shares sold (current_shares = 0)
- `"portfolio_only"` — has ledger history but NO symbol_config (legacy/imported)

**`portfolio` field:** null when no ledger history at all.

**`security` field:** null when no security_master record found.

**`holdings_by_account[]` notes:**
- Each entry has `account_id` (string), `shares` (string, required), `avg_cost_eur` (string or null)
- Computed per-account via the CMP holdings algorithm

**`recent_movements[]` notes:**
- `id` — ledger doc id
- `txn_type` — BUY | SELL | DIVIDEND | TRANSFER_IN | TRANSFER_OUT
- `trade_date` — ISO date string
- `quantity` — string or null
- `gross_eur` — EUR amount string or null (from `gross.eur_amount` or `net_eur`)

**300 Multiple Choices (bare ticker ambiguity):**
```json
{
  "multiple_choices": [
    { "security_id": "XMAD:SAN", "company_name": "Banco Santander", "exchange_mic": "XMAD" },
    { "security_id": "XPAR:SAN", "company_name": "Sanofi SA", "exchange_mic": "XPAR" }
  ],
  "candidates": [ ... ],
  "choices": [ ... ],
  "query": "SAN"
}
```
`multiple_choices`, `candidates`, and `choices` contain identical arrays. Use `multiple_choices`.

**404:**
```json
{ "error": "Symbol XNYS:FAKE not found" }
```

---

## 3. POST /api/symbols/add — Unified Add Symbol

**Request (select existing):**
```json
{ "security_id": "XNYS:AAPL" }
```

**Request (create new):**
```json
{
  "create": {
    "ticker": "AAPL",
    "exchange_mic": "XNYS",
    "company_name": "Apple Inc.",
    "listing_currency": "USD"
  }
}
```

**200 (select) / 201 (create):**
```json
{
  "security": { "security_id": "XNYS:AAPL", "ticker": "AAPL", "company_name": "Apple Inc.", ... },
  "config_created": true,
  "config_existed": false,
  "config_warning": null,
  "navigate_to": "/symbols/XNYS:AAPL"
}
```

**409 collision:**
```json
{
  "error": "collision",
  "field": "ticker",
  "detail": "ticker collision with existing security",
  "existing": { "security_id": "XNYS:AAPL", ... },
  "existing_security": { "security_id": "XNYS:AAPL", ... }
}
```
Both `existing` and `existing_security` are present and identical. Use `existing_security`.

---

## 4. GET /api/securities/search — Security Catalog Search

**URL:** `/api/securities/search?q=apple&limit=10`

**200:**
```json
{
  "candidates": [
    {
      "security_id": "XNYS:AAPL",
      "ticker": "AAPL",
      "company_name": "Apple Inc.",
      "exchange_mic": "XNYS",
      "has_config": true
    }
  ]
}
```
`has_config: true` = already in watchlist/portfolio → show "Already added" badge.

---

## 5–7. Admin Endpoints (unchanged from prior contract)

### GET /api/admin/symbol-config-backfill — Backfill Dry Run
```json
{
  "dry_run": true,
  "total_portfolio_securities": 5,
  "already_have_config": 3,
  "missing_config": 2,
  "missing": [{ "security_id": "XNYS:XYZ", "ticker": "XYZ", "company_name": "XYZ Corp" }],
  "collision_warnings": []
}
```

### POST /api/admin/symbol-config-backfill — Confirmed Execution
Body: `{ "confirm": true }`

### GET /api/admin/total-shares-reconciliation — Read-only Report
```json
{
  "reconciliation": [
    {
      "ticker": "AAPL", "security_id": "XNYS:AAPL",
      "config_total_shares": 150, "portfolio_derived_shares": "150.000000",
      "delta": "0.000000", "status": "match"
    }
  ],
  "summary": { "total_symbols": 3, "matched": 1, "mismatched": 1, "no_portfolio_data": 1 }
}
```
Status values: `"match"` | `"mismatch"` | `"no_portfolio_data"`

---

## Enrollment Warnings on Ledger Writes (existing endpoints, updated)

`POST /api/import/sessions/{id}/commit`:
```json
{
  "committed": [...],
  "enrolled_security_ids": ["XNYS:AAPL"],
  "enrollment_warnings": []
}
```

`POST /api/portfolio/movements`, `POST /api/portfolio/transfers` — no new fields; ensure happens silently with warning logged if it fails.

---

## Error Shape
```json
{ "error": "snake_case_code", "detail": "Human readable message" }
```
Codes: `not_found` (404), `validation_error` (400), `storage_unavailable` (503), `ambiguous_ticker` (300), `collision` (409), `internal_error` (500), `confirmation_required` (400).

---

## Null-safety Summary (matches Rusty's TypeScript types)

| Field | When null |
|---|---|
| `security` | No security_master record exists |
| `portfolio` | No ledger history at all |
| `symbol_state` | Never null — always one of four values |
| `portfolio_rows`/`watchlist_rows` absent | Frontend falls back to flat `rows` |
| `holdings_by_account` | Empty list `[]` if no account breakdown available |
| `portfolio_avg_cost_eur` / `portfolio_invested_eur` in overview | Null for watchlist-only rows |

