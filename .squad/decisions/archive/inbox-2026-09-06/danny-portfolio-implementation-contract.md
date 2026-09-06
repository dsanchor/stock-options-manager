# Implementation Contract: Portfolio Ledger, Securities Catalog, Conversational Import

**Date:** 2026-09-05  
**Version:** 1.1 (revised 2026-09-06)  
**Author:** Danny (Lead)  
**Status:** FROZEN — implementation-ready  
**Source design:** `.squad/designs/portfolio-ledger-securities-unified-design.md`  
**Decision ref:** `decisions.md` §1 (MVP Architecture) + §R1–R9 (Consolidated 2026-09-05)

---

## Change Log

| Version | Date | Summary |
|---------|------|---------|
| 1.0 | 2026-09-05 | Initial frozen contract |
| 1.1 | 2026-09-06 | 14 mandatory corrections — parsers renamed to domain schemas (not broker-specific); format enum domain-based; negative inventory is non-blocking warning; zero-price purchases are pending corporate-action acquisitions; `Importe en Derechos` persistent warning; broker/account optional (`_unassigned`); cross-batch duplicates are warnings; inline security create/map/exclude during chat; corporate-action source facts preserved; UI terminology corrected (Holdings ≠ Securities catalog); `total_shares` behavior preserved; storage fallback conventions followed |

---

## Phase 1 Scope — Smallest Complete Vertical Slice

Phase 1 delivers: **a user can paste a broker CSV into a chat interface, answer structured questions, review a preview, confirm, and see committed movements in a Portfolio section with derived holdings**.

### In-Scope (Phase 1)

1. **Security Master catalog** — `security_master` docs in `symbols` container
2. **Portfolio container** — `portfolio` Cosmos container (`/account_id` partition)
3. **Import Sessions container** — `import_sessions` Cosmos container (`/session_id` partition)
4. **Three domain CSV parsers** — `dividends`, `purchases`, `sales` (deterministic, no LLM); schemas match the user's exact historical spreadsheet columns (see §CSV Schemas below)
5. **Import session state machine** — CREATED → FILE_PARSED → BATCH_Q → ENTITY_Q → ROW_GROUP_Q → PREVIEW_READY → COMMITTED
6. **Import service** — question generation, entity resolution, staged rows, commit
7. **Inline security creation** — during ENTITY scope, user may create / map / exclude securities in chat before final ledger commit; atomic write to `symbols` + session update
8. **Account model** — optional, blank silently stored as `_unassigned`, assignable later; no warning when absent; no account management UI in Phase 1
9. **Ledger CRUD** — read movements, derived holdings, soft-delete
10. **Portfolio frontend** — navigation: **Holdings** view, **Movements** (ledger) view, **Import** chat UI; canonical **Securities** catalog evolves the existing Symbols area or occupies a separate route — it is NOT under the Portfolio nav
11. **Backward compatibility** — existing `total_shares` on `symbol_config` untouched; exact same behavior and APIs preserved; options behavior unchanged
12. **Storage fallback** — follow repository's existing best-effort container initialization pattern (`never blocks if missing`); if `portfolio` or `import_sessions` containers are not yet configured, the relevant operations return HTTP 503 with `{"error": "storage_unavailable", "detail": "…"}` — never silently claim persisted success

### Explicitly NOT in Phase 1 (do not implement)

- ❌ Manual movement entry forms (BUY/SELL/DIVIDEND forms — Phase 1b)
- ❌ Dividends sub-page (yield analytics — Phase 2)
- ❌ Accounts management UI (broker profile CRUD — Phase 2)
- ❌ `symbol_config.security_id` bridge field (Phase M2)
- ❌ HTTP 300 ticker collision handling (Phase M2; no collisions expected in Phase 1)
- ❌ Materialized views / snapshot caching (Phase 3)
- ❌ FIFO/LIFO cost basis (Phase 3)
- ❌ Fiscal export (Phase 4)
- ❌ Charts / analytics / time-series (Phase 5)
- ❌ ROW_QUESTIONS scope (per-row exceptions — edge case, Phase 1b)
- ❌ Full `ca_event` / `ca_leg` corporate action reconciliation docs (Phase 2; Phase 1 preserves source facts — see §Corporate Action Preservation)
- ❌ LLM orchestration layer for question generation (Phase 1 uses deterministic question generation only; LLM integration deferred)

---

## CSV Schemas — User's Exact Historical Columns

Parsers accept the user's own spreadsheet schemas — **not** broker-specific HeyTrade/ING formats. Spanish dates (`DD/MM/YYYY`) and decimal commas (`1.234,56`) throughout. Delimiter auto-detected: tab, semicolon, or comma.

### DIVIDENDS (first 8 columns)

| # | Column Header | Type | Notes |
|---|---------------|------|-------|
| 1 | `Año` | Integer (year) | Fiscal year |
| 2 | `Empresa` | String | Company name (free text, used for entity matching) |
| 3 | `Fecha de cobro` | Date `DD/MM/YYYY` | Payment / collection date |
| 4 | `Importe Bruto` | Decimal (comma) | Gross dividend amount |
| 5 | `Importe Neto` | Decimal (comma) | Net dividend amount |
| 6 | `Importe en Derechos` | Decimal (comma) | Rights/scrip amount — see §Derechos Warning |
| 7 | `Retención Origen` | Decimal (comma) | Source/origin withholding |
| 8 | `Retención Destino` | Decimal (comma) | Destination withholding |

Additional columns beyond column 8 may exist and must be preserved as raw source data but are not parsed into ledger fields.

**§Derechos Warning:** When `Importe en Derechos > 0`, the row must carry a persistent `RIGHTS_AMOUNT` reconciliation warning on the staged row and the committed movement. The system must **never** fabricate shares, infer a share count, or interpret the rights amount as a share acquisition. The warning persists until the user resolves it via a future corporate-action reconciliation flow (Phase 2).

### PURCHASES (7 columns)

| # | Column Header | Type | Notes |
|---|---------------|------|-------|
| 1 | `Año` | Integer (year) | Fiscal year |
| 2 | `Empresa` | String | Company name |
| 3 | `Fecha compra` | Date `DD/MM/YYYY` | Purchase date |
| 4 | `Valor compra` | Decimal (comma) | Price per share |
| 5 | `Acciones` | Decimal (comma) | Number of shares |
| 6 | `Total (€)` | Decimal (comma) | Total cost in EUR |
| 7 | `Comisión` | Decimal (comma) | Commission |

**§Zero-Price Purchases:** When `Valor compra = 0` (or blank) and `Acciones > 0`, the row represents a **pending corporate-action share acquisition** (e.g., scrip dividend, rights issue, stock split). Parser must:
- Preserve the quantity (`Acciones`) in the staged row
- Set `txn_type = "BUY"` with `cost_basis_status = "INCOMPLETE"`
- Set `price_per_share = 0` and `total_cost = 0` (source facts, not fabricated)
- Add a persistent `ZERO_COST_ACQUISITION` warning: "Shares acquired at zero cost — likely corporate action; cost basis incomplete"
- **Never** fabricate a cost basis or treat it as an ordinary zero-cost BUY

### SALES (6 columns)

| # | Column Header | Type | Notes |
|---|---------------|------|-------|
| 1 | `Año` | Integer (year) | Fiscal year |
| 2 | `Empresa` | String | Company name |
| 3 | `Fecha venta` | Date `DD/MM/YYYY` | Sale date |
| 4 | `Acciones` | Decimal (comma) | Number of shares sold |
| 5 | `Comisión` | Decimal (comma) | Commission |
| 6 | `Total Venta` | Decimal (comma) | Total sale proceeds in EUR |

---

## Negative Inventory Policy

Sales before purchases **are allowed**. When a SELL movement reduces derived holdings below zero for a given security:

1. **NON-BLOCKING warning** — `NEGATIVE_INVENTORY`: "Holdings for {security} would be negative ({n} shares). This may self-heal when earlier purchases are imported."
2. The warning appears in the preview but does **not** block commit.
3. The warning **self-heals** automatically: when later imports add BUY movements with earlier trade dates, derived holdings recompute and the warning no longer applies.
4. Holdings view may display negative share counts; this is expected during incremental imports.

**Rationale:** Users import files incrementally (e.g., sales 2024 before purchases 2020). Blocking sales on current holdings would make incremental import impossible.

---

## Corporate Action Preservation (Phase 1 Storage)

Full corporate-action reconciliation (`ca_event` / `ca_leg` docs) is deferred to Phase 2. However, Phase 1 **must preserve all source facts** needed for future reconciliation:

| Source Signal | Phase 1 Storage | Status Field |
|---------------|-----------------|--------------|
| `Valor compra = 0` + `Acciones > 0` | `ledger_txn` with `cost_basis_status = "INCOMPLETE"` | `ZERO_COST_ACQUISITION` warning |
| `Importe en Derechos > 0` | `ledger_txn` with `source_derechos_amount` field | `RIGHTS_AMOUNT` warning |
| All raw CSV row data | Preserved in `staged_import_row.source_row` (JSON) | — |

Warnings persist on committed movements and surface in Holdings and Movements views with a ⚠️ indicator until resolved.

---

## Frozen Endpoint Contracts

### Securities Catalog

#### `GET /api/securities`
Returns all `security_master` documents.

**Response 200:**
```json
{
  "securities": [
    {
      "security_id": "XNYS:AAPL",
      "ticker": "AAPL",
      "company_name": "Apple Inc.",
      "exchange_mic": "XNYS",
      "asset_class": "Equity",
      "listing_currency": "USD",
      "isin": "US0378331005",
      "status": "ACTIVE"
    }
  ]
}
```

#### `POST /api/securities`
Create a new `security_master`. Used by inline import creation and standalone.

**Request:**
```json
{
  "ticker": "AAPL",
  "company_name": "Apple Inc.",
  "exchange_mic": "XNYS",
  "asset_class": "Equity",
  "listing_currency": "USD",
  "isin": "US0378331005",
  "cusip": "037833100",
  "sedol": "2046251",
  "aliases": [{ "source": "user", "value": "Apple" }]
}
```

**Response 201:** Created `security_master` document.  
**Response 409:** ISIN or `security_id` collision — `{ "error": "collision", "existing": {...} }`

#### `GET /api/securities/{security_id}`
Lookup by `MIC:TICKER` (URL-encoded colon: `XNYS%3AAAPL`).

**Response 200:** Single `security_master` document.  
**Response 404:** Not found.

---

### Import Sessions

#### `POST /api/import/sessions`
Create a new import session and upload CSV content.

**Request (multipart/form-data):**
- `file`: CSV file upload
- `format_hint` (optional): `"dividends"` | `"purchases"` | `"sales"` — if omitted, auto-detection from column headers
- `currency` (optional): ISO 4217 code, default `"EUR"` — batch currency for all rows
- `account_id` (optional): broker/account identifier, default `"_unassigned"` — no warning when blank

**Response 201:**
```json
{
  "session_id": "imp_<uuid>",
  "state": "FILE_PARSED",
  "row_count": 42,
  "detected_format": "dividends",
  "currency": "EUR",
  "account_id": "_unassigned",
  "warnings": [
    { "type": "NEGATIVE_INVENTORY", "security": "XNYS:AAPL", "shares": "-50", "message": "..." },
    { "type": "ZERO_COST_ACQUISITION", "row_index": 12, "company": "Telefónica", "message": "..." },
    { "type": "RIGHTS_AMOUNT", "row_index": 7, "company": "Santander", "amount": "45,30", "message": "..." }
  ],
  "questions": [...],
  "staged_summary": { "total_rows": 42, "currencies": ["EUR"], "date_range": ["2024-01-15", "2025-06-30"] }
}
```

**Response 400:** Parse failure — `{ "error": "parse_error", "detail": "..." }`

#### `GET /api/import/sessions/{session_id}`
Get current session state, all questions, and staged summary.

**Response 200:**
```json
{
  "session_id": "imp_<uuid>",
  "state": "ENTITY_QUESTIONS",
  "questions": [
    {
      "question_id": "q_<uuid>",
      "scope": "ENTITY",
      "company_name": "Apple Inc.",
      "normalized_name": "apple inc",
      "candidates": [
        { "security_id": "XNYS:AAPL", "company_name": "Apple Inc.", "score": 0.95 }
      ],
      "answer": null
    }
  ],
  "staged_summary": { "total_rows": 42, "resolved_rows": 28, "unresolved_rows": 14 }
}
```

#### `POST /api/import/sessions/{session_id}/answers`
Submit one answer to a question. Returns updated session state.

**Request:**
```json
{
  "question_id": "q_<uuid>",
  "answer_type": "SELECTED_CANDIDATE",
  "selected_security_id": "XNYS:AAPL"
}
```

**`answer_type` enum:** `"SELECTED_CANDIDATE"` | `"CREATED_NEW_SECURITY"` | `"SKIPPED_COMPANY"` | `"EXCLUDED_COMPANY"` | `"BATCH_VALUE"`

- `SELECTED_CANDIDATE`: Map company name to an existing security
- `CREATED_NEW_SECURITY`: Inline-create a new `security_master` doc and map to it (see §Inline Security Creation)
- `SKIPPED_COMPANY`: Skip all rows for this company in this import (rows not committed)
- `EXCLUDED_COMPANY`: Permanently exclude this company name from future imports (alias stored)
- `BATCH_VALUE`: Answer a batch-level question (currency, account, etc.)

**Response 200:** Updated session (same shape as GET).  
**Response 400:** Invalid answer — `{ "error": "invalid_answer", "detail": "..." }`  
**Response 404:** Session not found or expired.  
**Response 409:** Session in terminal state.

#### `POST /api/import/sessions/{session_id}/preview`
Generate commit preview. Only valid when all questions answered and no blocking issues.

**Response 200:**
```json
{
  "session_id": "imp_<uuid>",
  "state": "PREVIEW_READY",
  "preview": {
    "movements": [
      {
        "row_index": 0,
        "txn_type": "DIVIDEND",
        "security_id": "XNYS:AAPL",
        "ticker": "AAPL",
        "company_name": "Apple Inc.",
        "trade_date": "2024-06-15",
        "quantity": "100",
        "gross_eur": "86.25",
        "fees_eur": "0.00",
        "wht_source_eur": "12.94",
        "net_eur": "73.31"
      }
    ],
    "warnings": [
      { "type": "NEGATIVE_INVENTORY", "security_id": "XMAD:TEF", "shares": "-200", "message": "..." },
      { "type": "ZERO_COST_ACQUISITION", "row_index": 12, "security_id": "XMAD:TEF", "message": "..." },
      { "type": "RIGHTS_AMOUNT", "row_index": 7, "security_id": "XMAD:SAN", "amount": "45.30", "message": "..." },
      { "type": "PROBABLE_DUPLICATE", "row_indices": [3, 4], "existing_movement_id": "txn_...", "message": "Possible duplicate of previously committed movement" }
    ],
    "total_movements": 42,
    "skipped_rows": 3,
    "skip_reasons": [{ "company": "Unknown Corp", "reason": "SKIPPED_COMPANY", "row_count": 3 }]
  }
}
```

**Response 409:** Unresolved questions remain — `{ "error": "unresolved_questions", "pending": [...] }`

#### `POST /api/import/sessions/{session_id}/commit`
Commit previewed movements to ledger. Idempotent (dedup key).

**Response 200:**
```json
{
  "session_id": "imp_<uuid>",
  "state": "COMMITTED",
  "committed_count": 39,
  "skipped_count": 3
}
```

**Response 409:** Already committed — `{ "error": "already_committed" }`  
**Response 409:** Not in PREVIEW_READY — `{ "error": "invalid_state", "current": "..." }`

---

### Portfolio (Ledger & Holdings)

#### `GET /api/portfolio/holdings`
Derived holdings computed from ledger. Grouped by security.

**Query params:** `account_id` (optional, default: all accounts)

**Response 200:**
```json
{
  "holdings": [
    {
      "security_id": "XNYS:AAPL",
      "ticker": "AAPL",
      "company_name": "Apple Inc.",
      "total_shares": "150.000000",
      "avg_cost_basis_eur": "142.35",
      "cost_basis_status": "COMPLETE",
      "total_invested_eur": "21352.50",
      "total_dividends_eur": "1245.60",
      "accounts": ["_unassigned"],
      "warnings": []
    },
    {
      "security_id": "XMAD:TEF",
      "ticker": "TEF",
      "company_name": "Telefónica",
      "total_shares": "-200.000000",
      "avg_cost_basis_eur": null,
      "cost_basis_status": "INCOMPLETE",
      "total_invested_eur": "0.00",
      "total_dividends_eur": "320.00",
      "accounts": ["_unassigned"],
      "warnings": [
        { "type": "NEGATIVE_INVENTORY", "message": "Negative holdings — earlier purchases may not yet be imported" },
        { "type": "ZERO_COST_ACQUISITION", "count": 2, "message": "2 acquisitions with incomplete cost basis" }
      ]
    }
  ],
  "summary": {
    "total_securities": 8,
    "total_invested_eur": "85000.00",
    "total_dividends_eur": "6200.00"
  }
}
```

#### `GET /api/portfolio/movements`
Paginated ledger entries.

**Query params:** `account_id`, `security_id`, `txn_type`, `date_from`, `date_to`, `limit` (default 50), `offset` (default 0)

**Response 200:**
```json
{
  "movements": [
    {
      "id": "txn_unassigned_20240615_AAPL_DIVIDEND_001",
      "txn_type": "DIVIDEND",
      "security_id": "XNYS:AAPL",
      "ticker": "AAPL",
      "company_name": "Apple Inc.",
      "trade_date": "2024-06-15",
      "quantity": "100.000000",
      "gross": { "amount": "100.00", "currency": "USD", "eur_amount": "86.25" },
      "fees": { "total": "0.00", "currency": "USD", "total_eur": "0.00" },
      "withholding": {
        "source": { "country": "US", "rate_pct": "15.00", "amount_eur": "12.94" },
        "destination": null
      },
      "net": { "amount": "85.00", "currency": "USD", "eur_amount": "73.31" },
      "fx": { "rate": "0.862500000", "rate_source": "ECB" },
      "account_id": "_unassigned",
      "import_source": "csv_import",
      "created_at": "2026-09-06T..."
    }
  ],
  "total_count": 42,
  "limit": 50,
  "offset": 0
}
```

#### `DELETE /api/portfolio/movements/{movement_id}`
Soft-delete a movement (sets `deleted_at`).

**Response 200:** `{ "id": "...", "deleted_at": "..." }`  
**Response 404:** Not found.

---

## Frozen Enums

```
TxnType          = "BUY" | "SELL" | "DIVIDEND"
ImportFormat     = "dividends" | "purchases" | "sales"
CostBasisStatus  = "COMPLETE" | "INCOMPLETE"
WarningType      = "NEGATIVE_INVENTORY" | "ZERO_COST_ACQUISITION" | "RIGHTS_AMOUNT" | "PROBABLE_DUPLICATE"
SessionState     = "CREATED" | "FILE_PARSED" | "BATCH_QUESTIONS" | "ENTITY_QUESTIONS" |
                   "ROW_GROUP_QUESTIONS" | "PREVIEW_READY" | "COMMIT_CONFIRMED" | "COMMITTED" | "EXPIRED"
QuestionScope    = "BATCH" | "ENTITY" | "ROW_GROUP"
AnswerType       = "SELECTED_CANDIDATE" | "CREATED_NEW_SECURITY" | "SKIPPED_COMPANY" | "EXCLUDED_COMPANY" | "BATCH_VALUE"
AssetClass       = "Equity"
SecurityStatus   = "ACTIVE" | "DELISTED"
FxRateSource     = "ECB" | "BROKER" | "MANUAL"
ImportSource     = "csv_import" | "manual"
```

---

## Frozen Error Handling

| HTTP Status | `error` field | Meaning |
|-------------|---------------|---------|
| 400 | `parse_error` | CSV parse failure (bad format, encoding, empty, unrecognized columns) |
| 400 | `invalid_answer` | Answer doesn't match question type or candidates |
| 400 | `validation_error` | Field validation failure (missing required fields) |
| 404 | `not_found` | Resource does not exist or session expired |
| 409 | `collision` | Security ISIN or security_id already exists |
| 409 | `already_committed` | Import session already committed (idempotent) |
| 409 | `invalid_state` | Session not in correct state for operation |
| 409 | `unresolved_questions` | Cannot preview/commit with unanswered questions |
| 500 | `internal_error` | Unexpected server error |
| 503 | `storage_unavailable` | Required Cosmos container not configured — never silently claim success |

All error responses: `{ "error": "<code>", "detail": "<human message>" }`

---

## Agent Assignments

### Livingston — Backend Owner

**Files owned (all new, no conflicts):**

| Path | Purpose |
|------|---------|
| `backend/src/portfolio/` | New package directory |
| `backend/src/portfolio/__init__.py` | Package init |
| `backend/src/portfolio/models.py` | Pydantic models: SecurityMaster, LedgerTxn, ImportSession, Question, StagedImportRow, Holdings |
| `backend/src/portfolio/cosmos_portfolio.py` | Cosmos operations for `portfolio` and `import_sessions` containers |
| `backend/src/portfolio/cosmos_securities.py` | Cosmos operations for `security_master` docs in `symbols` container |
| `backend/src/portfolio/parsers/__init__.py` | Parser package |
| `backend/src/portfolio/parsers/dividends.py` | Dividends CSV parser (Año, Empresa, Fecha de cobro, …) |
| `backend/src/portfolio/parsers/purchases.py` | Purchases CSV parser (Año, Empresa, Fecha compra, …) |
| `backend/src/portfolio/parsers/sales.py` | Sales CSV parser (Año, Empresa, Fecha venta, …) |
| `backend/src/portfolio/parsers/common.py` | Shared parsing utilities (Spanish locale numbers, date normalization, delimiter auto-detection) |
| `backend/src/portfolio/import_service.py` | Import session orchestration, question generation, state transitions |
| `backend/src/portfolio/holdings_service.py` | Derived holdings computation from ledger |
| `backend/web/portfolio_routes.py` | FastAPI router: all `/api/portfolio/*`, `/api/securities/*`, `/api/import/*` endpoints |
| `backend/tests/test_portfolio_parsers.py` | Parser unit tests |
| `backend/tests/test_portfolio_import_service.py` | Import service unit tests (state machine, question generation) |
| `backend/tests/test_portfolio_holdings.py` | Holdings derivation tests |
| `backend/tests/test_portfolio_endpoints.py` | API endpoint integration tests |
| `backend/tests/test_securities_catalog.py` | Security CRUD + collision tests |

**Existing file touches (minimal, additive only):**

| File | Change |
|------|--------|
| `backend/web/app.py` | Add ONE line: `app.include_router(portfolio_router)` to mount the new router |
| `backend/src/cosmos_db.py` | Add container init for `portfolio` and `import_sessions` (new methods, no existing method changes) |

**Must NOT change:** Any existing endpoint behavior, `total_shares` field semantics, `symbol_config` doc structure, any existing test.

### Rusty — Frontend Owner

**Files owned (all new, no conflicts):**

| Path | Purpose |
|------|---------|
| `frontend/src/types/portfolio.ts` | TypeScript types: SecurityMaster, LedgerMovement, Holdings, ImportSession, Question, etc. |
| `frontend/src/types/import.ts` | Import-specific types: session state, questions, answers, preview |
| `frontend/src/lib/portfolio-api.ts` | Typed API client for all portfolio/securities/import endpoints |
| `frontend/src/app/portfolio/page.tsx` | Portfolio landing → redirect to /portfolio/holdings |
| `frontend/src/app/portfolio/holdings/page.tsx` | Holdings view (shares owned, derived from ledger) |
| `frontend/src/app/portfolio/movements/page.tsx` | Movements (ledger) view with filters |
| `frontend/src/app/portfolio/import/page.tsx` | Import chat page |
| `frontend/src/components/PortfolioHoldingsTable.tsx` | Holdings table component |
| `frontend/src/components/PortfolioMovementsTable.tsx` | Movements table with pagination |
| `frontend/src/components/ImportChat.tsx` | Conversational import chat UI |
| `frontend/src/components/ImportQuestionCard.tsx` | Individual question rendering (BATCH, ENTITY, ROW_GROUP) |
| `frontend/src/components/ImportPreview.tsx` | Commit preview table |
| `frontend/src/components/SecurityCreateForm.tsx` | Inline security creation sub-form |
| `frontend/src/app/api/portfolio/[...slug]/route.ts` | Next.js API proxy for `/api/portfolio/*` |
| `frontend/src/app/api/securities/[...slug]/route.ts` | Next.js API proxy for `/api/securities/*` |
| `frontend/src/app/api/import/[...slug]/route.ts` | Next.js API proxy for `/api/import/*` |

**Existing file touches (minimal, additive only):**

| File | Change |
|------|--------|
| `frontend/src/components/TopNav.tsx` | Add "Portfolio" dropdown between Economics and Chat with items: Holdings, Movements, Import |

**Must NOT change:** Any existing Symbols page, SymbolsTable, types/symbols.ts, or any `/api/symbols` proxy route.

**Mock-safe development:** Rusty may use mock data in `portfolio-api.ts` behind a `USE_MOCK` flag while backend is not yet available. All types must match the frozen contract shapes above. Remove mocks before merge.

---

## Shared-File Conflict Analysis

| File | Risk | Resolution |
|------|------|------------|
| `backend/web/app.py` | Both might touch | Livingston adds ONE router include line. No other agent touches app.py. |
| `backend/src/cosmos_db.py` | Livingston adds new methods | All additions are new methods; no existing method signature changes. |
| `frontend/src/components/TopNav.tsx` | Rusty adds Portfolio nav item | Only Rusty touches this file. |
| `.squad/*` | Dirty files exist | Neither agent modifies `.squad/` files. Danny owns ceremony docs only. |

**Zero conflict expected.** All production code is in new files per agent.

---

## Test Acceptance Criteria

### Non-Regression Gates (MUST pass before merge)

1. **All existing backend tests pass unchanged** — `cd backend && python -m pytest tests/ -x`
2. **`total_shares` behavior unchanged** — existing `GET /api/symbols/overview` returns `total_shares` from `symbol_config` as before; field is never computed from portfolio ledger; exact same API behavior and response shapes preserved
3. **Frontend build succeeds** — `cd frontend && npm run build`
4. **No existing endpoint response shape changes** — `/api/symbols/*` routes return identical payloads

### New Test Requirements

#### Livingston's Backend Tests

| Test | Criteria |
|------|----------|
| `test_portfolio_parsers.py` | Each parser (`dividends`, `purchases`, `sales`): valid CSV → correct row count, amounts, dates, normalized company names. Invalid CSV → `parse_error`. Empty file → `parse_error`. Spanish locale numbers (comma decimal `1.234,56`) parsed correctly. Tab/semicolon/comma delimiter auto-detected. Zero-price purchase rows flagged with `ZERO_COST_ACQUISITION` warning. Dividend rows with `Importe en Derechos > 0` flagged with `RIGHTS_AMOUNT` warning. |
| `test_portfolio_import_service.py` | State machine transitions: CREATED→FILE_PARSED→BATCH_Q→ENTITY_Q→PREVIEW_READY→COMMITTED. Invalid transitions rejected. Answer fan-out: entity answer applies to all rows with same company. Skipped company excludes rows from commit. Negative inventory is non-blocking warning (does not reject commit). Cross-batch probable duplicates surface as warnings requiring confirmation. Same-session idempotency uses `batch+row+normalized_hash`. Inline security create/map/exclude during entity resolution. |
| `test_portfolio_holdings.py` | `total_shares = SUM(BUY) - SUM(SELL)`. Negative holdings emit `NEGATIVE_INVENTORY` warning (non-blocking, not validation error). Multiple accounts: each independent. Empty ledger → empty holdings. Zero-cost acquisitions have `cost_basis_status = "INCOMPLETE"`. |
| `test_portfolio_endpoints.py` | All endpoints return frozen shapes. 400/404/409/503 error codes correct. Pagination works. Soft-delete sets `deleted_at` and excludes from aggregates. Storage-unavailable returns 503 not silent success. Warnings included in preview and holdings responses. |
| `test_securities_catalog.py` | Create security → 201. Duplicate ISIN → 409. Duplicate security_id → 409. List all → includes new. Alias storage works. |

#### Rusty's Frontend Tests

| Test | Criteria |
|------|----------|
| Build-time type check | `npm run build` succeeds with all new types |
| Component render smoke | Portfolio pages render without crash (manual verification or future Playwright) |
| API client types | `portfolio-api.ts` functions have correct return types matching frozen contract |

---

## Implementation Sequence

### Parallel Track (no dependencies between agents)

```
Livingston (backend)                    Rusty (frontend)
─────────────────────                   ──────────────────
1. models.py (Pydantic)                 1. types/portfolio.ts + types/import.ts
2. cosmos_securities.py                 2. portfolio-api.ts (with mocks)
3. parsers/ (3 domain CSV schemas)     3. TopNav.tsx (add Portfolio: Holdings, Movements, Import)
4. import_service.py                    4. Portfolio pages (holdings, movements)
5. holdings_service.py                  5. ImportChat.tsx + question cards
6. cosmos_portfolio.py                  6. ImportPreview.tsx + SecurityCreateForm
7. portfolio_routes.py                  7. API proxy routes
8. app.py (one-line router mount)       8. Remove mocks, integration test
9. All backend tests                    9. Build verification
```

### Integration Point

After both agents complete, Danny verifies:
- Backend tests pass
- Frontend builds
- API proxy routes connect to backend endpoints
- End-to-end: upload CSV → questions → preview → commit → see holdings

---

## Contract Versioning

This contract is **v1.1 — frozen**. Changes require Danny's approval via a new decision document. Agents must not deviate from endpoint paths, request/response shapes, or enum values without written amendment.

---

## Deduplication Policy

### Same-Session Idempotency
Within a single import session, deduplication uses `batch_id + row_index + normalized_hash`. The normalized hash is computed from: `security_id + txn_type + trade_date + quantity + gross_amount`. Re-uploading the same file in the same session produces identical staged rows (idempotent).

### Cross-Batch Probable Duplicates
When committing, the service checks existing committed movements for probable duplicates (same `security_id + txn_type + trade_date + quantity + gross_amount`). Matches are surfaced as `PROBABLE_DUPLICATE` **warnings** in the preview — they **do not block** commit. The user must explicitly confirm or dismiss each duplicate warning before final commit.

---

## Multi-Market Security Compatibility

The Securities catalog supports securities from any exchange worldwide. Users may create securities spanning US (XNYS, XNAS), Madrid (XMAD), Amsterdam (XAMS), London (XLON), and other exchanges. The `exchange_mic` field accepts any valid ISO 10383 MIC code. Import entity matching works with any exchange; the system does not assume US-only or EU-only securities.

**Dirty `.squad` files (agents history, decisions.md, designs/) are not touched by this contract.** They remain as-is.
