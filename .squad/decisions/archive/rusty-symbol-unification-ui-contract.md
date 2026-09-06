# Frontend UI Contract — Symbol Unification Rev 3

**Date:** 2026-09-06  
**From:** Rusty (Frontend)  
**To:** Livingston (Backend)  
**Status:** Awaiting backend implementation; frontend ready

---

## API Shapes I'm Consuming — Please Confirm

### 1. `GET /api/securities/search?q=...&limit=...`

Expected response:
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

### 2. `POST /api/symbols/add`

Request (select existing):
```json
{ "security_id": "XNYS:AAPL" }
```
Request (create new):
```json
{ "create": { "ticker": "AAPL", "exchange_mic": "XNYS", "company_name": "Apple Inc.", "listing_currency": "USD", ... } }
```

Expected response (200 or 201):
```json
{
  "security": { "security_id": "XNYS:AAPL", "ticker": "AAPL", "company_name": "Apple Inc.", ... },
  "config_created": true,
  "config_existed": false,
  "config_warning": null,
  "navigate_to": "/symbols/XNYS:AAPL"
}
```

Error 409 (collision on create):
```json
{ "error": "...", "existing_security": { "security_id": "..." } }
```

### 3. `GET /api/symbols/{symbol}/detail` — new fields

Added to existing response:
```json
{
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
      { "account_id": "fidelity_main", "account_name": "Fidelity", "shares": "150.000000", "avg_cost_eur": "142.35" }
    ],
    "recent_movements": [
      { "id": "txn_...", "txn_type": "BUY", "trade_date": "2026-08-15", "quantity": "50", "gross_eur": "7200.00" }
    ],
    "movement_count": 12
  },
  "symbol_state": "watchlist_and_portfolio"
}
```

For disambiguation (300 Multiple Choices):
```json
{
  "multiple_choices": [
    { "security_id": "XMAD:SAN", "company_name": "Banco Santander", "exchange_mic": "XMAD" },
    { "security_id": "XPAR:SAN", "company_name": "Sanofi SA", "exchange_mic": "XPAR" }
  ],
  "query": "SAN"
}
```

### 4. `GET /api/symbols/overview` — new fields

Added to existing response:
```json
{
  "portfolio_rows": [ { ...existing fields..., "list_section": "portfolio", "security_id": "XNYS:AAPL", "portfolio_shares": "150.000000", "portfolio_avg_cost_eur": "142.35", "portfolio_invested_eur": "21352.50" } ],
  "watchlist_rows": [ { ...existing fields..., "list_section": "watchlist", "security_id": null, "portfolio_shares": null, "portfolio_avg_cost_eur": null, "portfolio_invested_eur": null } ],
  "rows": [ ... ],
  "portfolio_count": 18,
  "watchlist_count": 7
}
```

---

## BFF Proxy Routes Added

- `POST /api/symbols/add` → `src/app/api/symbols/add/route.ts` (NEW)
- `GET /api/securities/search` → already covered by `src/app/api/securities/[[...slug]]/route.ts`

---

## Frontend Behavior When Fields Are Absent (null-safe)

- `security` null → identity badge not shown; no regression
- `portfolio` null → Portfolio Holdings card and Recent Movements not shown; existing sections unaffected
- `symbol_state` null → treated as "watchlist_only" equivalent (all sections shown)
- `portfolio_rows`/`watchlist_rows` absent → falls back to flat `rows` rendering (no sections)

Frontend is ready; all sections are null-guarded against the pre-unification backend.
