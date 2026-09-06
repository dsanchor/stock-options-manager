# Symbol Unification — Implementation Contract

**Date:** 2026-09-06 (rev 3 — unified Add Symbol + two-list Symbols page)  
**Author:** Danny (Lead Architect)  
**Status:** ACCEPTED — user-authorized implementation  
**Directives consumed:**
- `copilot-directive-20260906-proceed-symbol-unification.md`
- `copilot-directive-20260906-add-security-creation.md` (superseded by unified directive below)
- `copilot-directive-20260906-unified-add-symbol.md` ← **authoritative; supersedes separate Add Security**
- `copilot-directive-20260906-watchlist-two-lists.md` ← **authoritative; two-section Symbols page**

---

## 1. `ensure_symbol_config(security_id, source)` — Idempotent Enrollment

### Semantics

A pure function that guarantees a `symbol_config` document exists in the `symbols` container for the given ticker. **Existing configs are never overwritten.** Returns the config (existing or newly created).

### Signature

```python
# backend/src/portfolio/symbol_config_sync.py (NEW file)

def ensure_symbol_config(
    symbols_container,          # Cosmos symbols container client
    security_id: str,           # "XNYS:AAPL"
    source: str,                # "import_commit" | "manual_movement" | "transfer_in" | "add_symbol" | "backfill"
) -> dict:
    """Idempotently ensure a symbol_config exists for the ticker extracted from security_id.

    Returns:
        Existing or newly created symbol_config document.
    
    Behavior:
        1. Extract ticker from security_id (e.g. "AAPL" from "XNYS:AAPL").
        2. Point-read `config_{ticker}` in partition `{ticker}`.
        3. If found → return as-is (no-op). Never modify existing config.
        4. If CosmosResourceNotFoundError → create with disabled defaults below.
        5. If create races with another writer (409 Conflict) → re-read and return.
    """
```

### Disabled Defaults (new config only)

```json
{
  "id": "config_{TICKER}",
  "symbol": "{TICKER}",
  "doc_type": "symbol_config",
  "security_id": "{MIC}:{TICKER}",
  "exchange": "{MIC}",
  "display_name": "{company_name from security_master}",
  "total_shares": 0,
  "watchlist": {
    "covered_call": false,
    "cash_secured_put": false,
    "buy_tracker": false
  },
  "telegram_notifications_enabled": false,
  "positions": [],
  "created_at": "{ISO8601}",
  "updated_at": "{ISO8601}",
  "_auto_enrolled": true,
  "_auto_enrolled_source": "{source}",
  "_auto_enrolled_at": "{ISO8601}"
}
```

**Key field:** `security_id` — new field on `symbol_config` that links it unambiguously to a `security_master`. Only set on auto-created configs; existing configs get it via backfill (§3) or user action.

**Key invariant:** `telegram_notifications_enabled: false`, all `watchlist.*: false`, empty `positions[]`. The user must explicitly opt in to any agent, alert, or notification.

### Error handling

| Scenario | Behavior |
|----------|----------|
| security_master not found for security_id | Raise `ValueError` — caller must have a valid security first |
| Cosmos 409 on create (race) | Re-read config; return existing |
| Cosmos transient error (429, 503) | Propagate; caller's retry logic handles |
| Existing config with different `security_id` | No-op; do not overwrite. Log warning for ticker collision visibility. |

---

## 2. Trigger Points — Who Calls `ensure_symbol_config` and When

### Design Principle

**Synchronous best-effort after authoritative ledger write, plus read-repair on holdings computation.** No fire-and-forget; no new message queue infrastructure.

### 2.1 Import Commit (`import_service.commit_session`)

**When:** After each `write_ledger_txn` succeeds (inside the commit loop).  
**How:** Collect distinct `security_id` values from committed movements. After the commit loop, call `ensure_symbol_config` for each unique `security_id`.  
**Failure mode:** If `ensure_symbol_config` fails for one security, log warning, continue with others. The holding will still exist; read-repair (§2.5) catches it next time holdings are computed.  
**No ledger rollback:** The ledger write is already committed. Config enrollment is a best-effort secondary write to a **different Cosmos container** (`symbols` vs `portfolio`). Cross-container transactional rollback is impossible and not attempted.

```python
# In import_service.py commit_session, after commit loop:
enrolled_ids = set()
for movement in committed_movements:
    sid = movement.get("security_id")
    if sid and sid not in enrolled_ids:
        try:
            ensure_symbol_config(symbols_container, sid, source="import_commit")
            enrolled_ids.add(sid)
        except Exception:
            logger.warning("ensure_symbol_config failed for %s during import commit", sid, exc_info=True)
```

### 2.2 Manual Movement (`create_manual_movement`)

**When:** After `write_ledger_txn` returns successfully.  
**How:** Single `ensure_symbol_config(security_id, source="manual_movement")`.  
**Failure mode:** Log warning; 201 still returned to user (ledger is authoritative).

### 2.3 Transfer-In (`create_transfer_pair`)

**When:** After `TRANSFER_IN` ledger write succeeds (the transfer pair creates TRANSFER_OUT + TRANSFER_IN; only the IN side triggers enrollment because the OUT side's ticker already has a config from prior BUY).  
**How:** `ensure_symbol_config(security_id, source="transfer_in")`.  
**Failure mode:** Same as §2.2.

### 2.4 Unified "Add Symbol" Flow (replaces standalone security creation)

**When:** Immediately after SecurityMaster is created or selected (see §6).  
**How:** `ensure_symbol_config(security_id, source="add_symbol")`.  
**Behavior on existing config:** No-op — return existing config unchanged. User explicitly chose an existing security; its config is already set up with whatever flags the user previously chose. **Never reset existing agent toggles.**  
**Failure mode:** If config creation fails, return 201 for the security but include a `config_warning` field. Frontend displays a non-blocking warning.

### 2.5 Read-Repair on Holdings Computation

**When:** `holdings_service.compute_holdings()` encounters a `security_id` that has no matching `symbol_config`.  
**How:** Call `ensure_symbol_config` for any `security_id` present in holdings but missing from configs.  
**Purpose:** Catches any enrollment that failed in §2.1–2.4. Self-healing; no manual intervention.  
**Performance:** Point-read per ticker (< 5ms each); only triggered for missing configs (typically zero after initial backfill).

---

## 3. Backfill — Dry-Run and Confirmed Execution

### 3.1 Dry-Run Endpoint

```
GET /api/admin/symbol-config-backfill?dry_run=true
```

**Logic:**
1. Query all distinct `security_id` values from `portfolio` container (cross-partition: `SELECT DISTINCT c.security.security_id FROM c WHERE c.doc_type = 'ledger_txn' AND NOT IS_DEFINED(c.deleted_at)`).
2. For each, extract ticker; point-read `config_{ticker}` in `symbols`.
3. Report:

```json
{
  "dry_run": true,
  "total_portfolio_securities": 42,
  "already_have_config": 38,
  "missing_config": 4,
  "missing": [
    { "security_id": "XMAD:SAN", "ticker": "SAN", "company_name": "Banco Santander" },
    { "security_id": "XAMS:ASML", "ticker": "ASML", "company_name": "ASML Holding" }
  ],
  "collision_warnings": [
    { "ticker": "SAN", "existing_config_security_id": null, "candidate_security_id": "XMAD:SAN",
      "note": "Existing config_SAN has no security_id; will not overwrite" }
  ]
}
```

**Gap criteria:** A security is "missing" when no `config_{ticker}` document exists. A security is a "collision warning" when `config_{ticker}` exists but its `security_id` field is absent or different.

### 3.2 Confirmed Execution Endpoint

```
POST /api/admin/symbol-config-backfill
Body: { "confirm": true }
```

**Logic:** Same scan as dry-run; for each missing config, call `ensure_symbol_config(security_id, source="backfill")`.

**Response:**

```json
{
  "dry_run": false,
  "created": 4,
  "skipped_existing": 38,
  "collision_warnings": [...],
  "errors": []
}
```

**Safety:** Existing configs are never modified. Collision warnings are reported but not auto-resolved. The `security_id` field is only written on newly created configs.

### 3.3 Backfill for `security_id` on Existing Configs

**This phase: report only.** The dry-run endpoint includes `collision_warnings` showing existing configs without `security_id`. No automatic write of `security_id` to existing configs. That migration is a future phase after user reviews the report.

---

## 4. Unified Symbol Details — Endpoint and Routing

### 4.1 Canonical Route

```
/symbols/{MIC}:{TICKER}     — e.g. /symbols/XNYS:AAPL
```

The Next.js dynamic segment `[symbol]` already accepts this (colon is valid in URL path segments).

### 4.2 Legacy Ticker-Only Route Resolution

```
GET /symbols/{TICKER}        — e.g. /symbols/AAPL
```

**Backend resolution in `/api/symbols/{symbol}/detail`:**

1. If `symbol` contains `:` → treat as `MIC:TICKER`; direct lookup.
2. If bare ticker → point-read `config_{TICKER}`:
   - If config exists and has `security_id` → use that for canonical identity.
   - If config exists without `security_id` → query `security_master` in partition `{TICKER}`:
     - Exactly 1 result → unambiguous; return detail.
     - Multiple results → return `300 Multiple Choices` with list of `{security_id, company_name, exchange_mic}`.
     - Zero results → legacy watchlist-only symbol; return current detail response (no portfolio section).
   - If no config → query `security_master`:
     - Exactly 1 → check if portfolio-only; return detail with portfolio data, no config sections.
     - Multiple → 300 Multiple Choices.
     - Zero → 404.

### 4.3 Unified Detail Response Shape

Extend the existing `/api/symbols/{symbol}/detail` response:

```json
{
  // Existing fields (unchanged)
  "symbol": "AAPL",
  "display_name": "Apple Inc.",
  "exchange": "XNYS",
  "total_shares": 100,
  "watchlist": { "covered_call": true, ... },
  "telegram_notifications_enabled": true,
  "enrichment": { ... },
  "positions": [ ... ],
  "activities": [ ... ],
  "plans": [ ... ],
  "summary": { ... },
  "next_earnings_date": "...",
  "is_paused": false,

  // NEW: canonical identity
  "security_id": "XNYS:AAPL",
  "security": {                          // null if no security_master exists (legacy watchlist-only)
    "security_id": "XNYS:AAPL",
    "company_name": "Apple Inc.",
    "exchange_mic": "XNYS",
    "isin": "US0378331005",
    "listing_currency": "USD",
    "status": "ACTIVE"
  },

  // NEW: portfolio holdings (null if no ledger history)
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

  // NEW: symbol state classification
  "symbol_state": "watchlist_and_portfolio"
  // Values: "watchlist_only" | "portfolio_only" | "watchlist_and_portfolio" | "portfolio_historical"
}
```

### 4.4 Frontend Redirect for Legacy URLs

The frontend `[symbol]/page.tsx` does NOT redirect. It passes the param to the API as-is. The API handles resolution. If the API returns `300 Multiple Choices`, the frontend renders a disambiguation page.

---

## 5. Frontend Sections — Conditional Behavior by Symbol State

### 5.1 State Classification

| `symbol_state` | Has `symbol_config` | Has ledger history | Current shares > 0 |
|-----------------|--------------------|--------------------|---------------------|
| `watchlist_only` | ✅ | ❌ | — |
| `portfolio_only` | ❌ (auto-created with all disabled) | ✅ | ≥ 0 |
| `watchlist_and_portfolio` | ✅ (pre-existing or auto-created) | ✅ | ≥ 0 |
| `portfolio_historical` | ✅ (auto-created, all disabled) | ✅ | 0 |

**Note:** `portfolio_only` is transient — `ensure_symbol_config` runs on every ledger write, so by the time the user sees the detail page, a config will exist. This state handles the case where read-repair hasn't run yet.

### 5.2 Section Visibility Matrix

| Section | watchlist_only | portfolio_only | watchlist_and_portfolio | portfolio_historical |
|---------|---------------|----------------|--------------------------|----------------------|
| TradingView widget | ✅ | ✅ | ✅ | ✅ |
| Symbol Summary (enrichment) | ✅ | ✅ | ✅ | ✅ |
| RT Chart | ✅ | ✅ | ✅ | ✅ |
| **Portfolio Holdings** card | ❌ | ✅ | ✅ | ✅ (shows "0 shares — historical") |
| **Recent Movements** table | ❌ | ✅ (last 5) | ✅ (last 5) | ✅ (last 5) |
| "View all movements" link | ❌ | ✅ → `/portfolio/movements?security_id=...` | ✅ | ✅ |
| Options Positions table | ✅ | ❌ (unless user enables agents) | ✅ | ❌ |
| Agent Activities | ✅ | ❌ | ✅ | ❌ |
| SymbolActions (watchlist toggles) | ✅ | ✅ (all off initially) | ✅ | ✅ |
| Plans | ✅ | ✅ | ✅ | ✅ |
| `total_shares` display | ✅ (editable) | ❌ (show portfolio-derived) | ✅ (show both if differ) | ✅ (show portfolio-derived "0") |

### 5.3 Disambiguation Page (300 Multiple Choices)

When `/symbols/SAN` resolves to multiple securities, render:

```
Multiple securities match "SAN"
┌─────────────────────────────┬──────────┬───────────────┐
│ Company                     │ Exchange │ Action        │
├─────────────────────────────┼──────────┼───────────────┤
│ Banco Santander             │ XMAD     │ [View →]      │
│ Sanofi SA                   │ XPAR     │ [View →]      │
└─────────────────────────────┴──────────┴───────────────┘
```

Each "View" links to `/symbols/XMAD:SAN` or `/symbols/XPAR:SAN`.

### 5.4 Symbols Page — Two Mutually Exclusive Sections

> **User directive (2026-09-06T16:24):** The Watchlist page must display two separate, mutually exclusive lists: (1) Portfolio securities (current or historical ledger presence) and (2) true Watchlist-only securities (manually added, no ledger presence). Portfolio membership takes display precedence — a symbol never appears in both lists. Both sections expose configuration controls.

#### 5.4.1 Classification Rule

For each `symbol_config` document:

1. **Query portfolio ledger** for any `ledger_txn` with matching `security_id` (or matching ticker if `security_id` not yet set on config). Include soft-deleted movements — presence means historical ownership.
2. **If any ledger history exists → Portfolio section.** Regardless of current share count, agent/notification state, or how the config was created. This includes zero-share/historical positions.
3. **If no ledger history → Watchlist section.** These are manually-added tracking-only symbols.

A symbol is classified into **exactly one** section. Portfolio takes precedence: if a user adds a symbol to the watchlist and later imports portfolio movements for it, the symbol migrates to the Portfolio section on next page load. This is not an error; it is the correct behavior.

#### 5.4.2 API Response — Extended `/api/symbols/overview`

```json
{
  "portfolio_rows": [
    {
      "symbol": "AAPL",
      "display_name": "Apple Inc.",
      "security_id": "XNYS:AAPL",
      "list_section": "portfolio",
      "portfolio_shares": "150.000000",
      "portfolio_avg_cost_eur": "142.35",
      "portfolio_invested_eur": "21352.50",
      // ... existing enrichment/options/watchlist fields unchanged
      "category": "Dividend Aristocrat",
      "dgi_score": 85,
      "tech_timing": 72,
      "entry_tag": "BUY",
      "momentum": "Bullish",
      "price": 198.50,
      "total_shares": 100,
      "active_count": 2,
      "in_calls": 100,
      "put_exposure": 19500,
      "call_exposure": 20000,
      "watchlist": { "covered_call": true, "cash_secured_put": true, "buy_tracker": false }
    }
  ],
  "watchlist_rows": [
    {
      "symbol": "MSFT",
      "display_name": "Microsoft Corp.",
      "security_id": null,
      "list_section": "watchlist",
      "portfolio_shares": null,
      "portfolio_avg_cost_eur": null,
      "portfolio_invested_eur": null,
      // ... existing enrichment/options/watchlist fields unchanged
      "category": "Tech Giant",
      "dgi_score": 78,
      "tech_timing": 65,
      "entry_tag": "",
      "momentum": "Neutral",
      "price": 420.00,
      "total_shares": 0,
      "active_count": 1,
      "in_calls": 0,
      "put_exposure": 0,
      "call_exposure": 0,
      "watchlist": { "covered_call": false, "cash_secured_put": false, "buy_tracker": true }
    }
  ],
  "rows": [ ... ],               // KEPT for backward compat: flat union of both, unsectioned
  "symbol_count": 25,
  "portfolio_count": 18,
  "watchlist_count": 7,
  "total_call_exposure": 45000,
  "total_put_exposure": 32000,
  "last_update_ts": "2026-09-06T..."
}
```

**Key design choices:**
- `portfolio_rows` and `watchlist_rows` are the authoritative sectioned arrays. `rows` kept for backward compat (existing `SymbolsTable` callers).
- Each row carries `list_section` ("portfolio" | "watchlist") for explicit classification.
- Portfolio rows carry `portfolio_shares`, `portfolio_avg_cost_eur`, `portfolio_invested_eur` (derived from holdings computation). Watchlist rows have these as `null`.
- `security_id` is included when available (from config or security_master lookup). Watchlist-only symbols without a SecurityMaster have `null`.
- All existing fields (`dgi_score`, `tech_timing`, `entry_tag`, `momentum`, `price`, `total_shares`, options exposure, `watchlist` flags) remain on every row regardless of section.

#### 5.4.3 Backend Implementation

In `_compute_symbols_overview` (`app.py`):

1. Load all `symbol_config` docs (existing behavior).
2. Compute portfolio holdings via `HoldingsService.compute_holdings()` (new dependency).
3. Build a `set` of tickers that have any holdings history (even zero-share).
4. Partition `symbol_config` rows: tickers in the holdings set → `portfolio_rows`; others → `watchlist_rows`.
5. Enrich `portfolio_rows` with `portfolio_shares`, `portfolio_avg_cost_eur`, `portfolio_invested_eur` from holdings.
6. `rows` = `portfolio_rows + watchlist_rows` (flat, backward compat).

**Performance note:** `compute_holdings` is already called on the Holdings page; the query is a cross-partition scan but cached at the application level within a single request. For the overview page, this is one additional cross-partition query (acceptable for <500 movements; same caveat as Holdings page).

#### 5.4.4 Frontend Layout

```
┌──────────────────────────────────────────────────────────┐
│ Symbols                               [+ Add Symbol]     │
│ 25 tracked · Calls $45,000 · Puts $32,000                │
├──────────────────────────────────────────────────────────┤
│                                                          │
│ ▼ Portfolio (18)                                         │
│ ┌──────────┬───────┬─────┬─────┬───────┬────────┬──────┐│
│ │ Symbol   │ Shares│ Cost│ DGI │ Tech  │ Entry  │ ...  ││
│ ├──────────┼───────┼─────┼─────┼───────┼────────┼──────┤│
│ │ AAPL     │ 150   │€142 │ 85  │  72   │ BUY    │      ││
│ │ O        │   0   │ —   │ 91  │  58   │        │      ││  ← historical, 0 shares
│ └──────────┴───────┴─────┴─────┴───────┴────────┴──────┘│
│                                                          │
│ ▼ Watchlist (7)                                          │
│ ┌──────────┬─────┬─────┬───────┬────────┬──────────────┐│
│ │ Symbol   │ DGI │ Tech│ Entry │ Mom.   │ Puts $       ││
│ ├──────────┼─────┼─────┼───────┼────────┼──────────────┤│
│ │ MSFT     │ 78  │  65 │       │Neutral │              ││
│ └──────────┴─────┴─────┴───────┴────────┴──────────────┘│
└──────────────────────────────────────────────────────────┘
```

**Column differences:**
- **Portfolio section** adds: Shares (portfolio-derived), Avg Cost (EUR), Invested (EUR). These replace the legacy `total_shares` column for this section.
- **Watchlist section** retains the current column set (including `total_shares` as editable manual field).
- Both sections show: Symbol, DGI, Tech, Entry, Momentum, Price, In Calls, Puts $, configuration controls.
- Both sections are collapsible (open by default). Section headers show count.
- Sort and filter (search bar, suitability pills) apply independently within each section.

#### 5.4.5 Edge Cases

| Scenario | Behavior |
|----------|----------|
| Symbol has ledger history + agents enabled | Portfolio section. Agents/enrichment data shown normally. |
| Symbol added via "Add Symbol" (no ledger) then later imported | Moves from Watchlist to Portfolio on next page load. No user action needed. |
| Symbol has only soft-deleted ledger movements | Portfolio section (ledger presence includes soft-deleted). |
| Symbol has `symbol_config` but no `security_master` and no ledger | Watchlist section (legacy pre-unification symbol). |
| New auto-enrolled config from import (all disabled) | Portfolio section (has ledger by definition). |

---

## 6. Unified "Add Symbol" Flow (ONE experience)

> **User directive (2026-09-06T16:21):** There must be ONE experience only. "Add Symbol" IS the SecurityMaster create/select flow. No separate "Add Security" button or UX. Every newly added symbol starts fully disabled. Existing `symbol_config` remains untouched when selecting an existing SecurityMaster.

### 6.1 UI — Single "Add Symbol" Button (replaces current)

The existing `AddSymbolForm` on `/symbols` is **replaced** (not supplemented) with a new unified `AddSymbolForm` that:

1. Offers a **search-first** experience: user types a ticker or company name.
2. Backend returns candidates from `security_master` catalog.
3. **If a match exists** → user selects it → `ensure_symbol_config` (no-op if config exists; create-disabled if not) → navigate to `/symbols/{MIC}:{TICKER}`.
4. **If no match** → user clicks "Create new security" → inline `SecurityCreateForm` fields expand (ticker, MIC, company name, currency, optional ISIN/CUSIP/SEDOL/Yahoo Finance symbol — reusing existing `SecurityCreateForm` field logic and `suggestYfinanceSymbol`) → creates SecurityMaster + `ensure_symbol_config` → navigate to `/symbols/{MIC}:{TICKER}`.

**Key UX rules:**
- No agent/notification toggles in the Add form. Everything starts disabled.
- The user enables agents later via `SymbolActions` on the Symbol Detail page.
- The old `AddSymbolForm` exchange-only field and CC/CSP/Buy tracker toggles are removed from the add flow.
- The old `POST /api/symbols` "create symbol_config directly" path remains functional for backward compatibility but the frontend no longer calls it from the Add Symbol form.

### 6.2 Backend Endpoint

```
POST /api/symbols/add
```

**Single endpoint that handles both "select existing" and "create new":**

```python
async def add_symbol(request: Request):
    """Unified Add Symbol: create-or-select SecurityMaster + ensure symbol_config.
    
    Body (select existing):
      { "security_id": "XNYS:AAPL" }
    
    Body (create new):
      { "create": { "ticker": "AAPL", "exchange_mic": "XNYS", "company_name": "Apple Inc.",
                     "listing_currency": "USD", ... } }
    """
    body = await request.json()
    
    if body.get("security_id"):
        # SELECT existing SecurityMaster
        security_id = body["security_id"]
        security = securities_svc.get_security(security_id)
        if security is None:
            return _err("not_found", f"Security {security_id} not found", 404)
    elif body.get("create"):
        # CREATE new SecurityMaster
        security = securities_svc.create_security(body["create"])  # raises _CollisionError → 409
        security_id = security["security_id"]
    else:
        return _err("validation_error", "Provide security_id or create object", 400)

    # ensure symbol_config (idempotent)
    config_warning = None
    try:
        config = ensure_symbol_config(symbols_container, security_id, source="add_symbol")
    except Exception as exc:
        logger.warning("ensure_symbol_config failed for add_symbol %s: %s", security_id, exc)
        config_warning = str(exc)
        config = None

    return JSONResponse({
        "security": _clean(security),
        "config_created": config is not None and config.get("_auto_enrolled"),
        "config_existed": config is not None and not config.get("_auto_enrolled"),
        "config_warning": config_warning,
        "navigate_to": f"/symbols/{security_id}",
    }, status_code=201 if body.get("create") else 200)
```

### 6.3 Search/Suggest Endpoint

```
GET /api/securities/search?q={query}&limit=10
```

Returns matching `security_master` documents by ticker, company name, or alias. Already partially supported via `find_candidates_for_name`; needs a thin route wrapper.

```json
{
  "candidates": [
    { "security_id": "XNYS:AAPL", "ticker": "AAPL", "company_name": "Apple Inc.", "exchange_mic": "XNYS", "has_config": true },
    { "security_id": "XNAS:AAPL", "ticker": "AAPL", "company_name": "Apple Inc.", "exchange_mic": "XNAS", "has_config": false }
  ]
}
```

The `has_config` field tells the frontend whether a `symbol_config` already exists (for display purposes: "Already in watchlist" badge).

### 6.4 Duplicate/Collision Handling

- **Selecting existing security that already has config:** No-op on config. 200 response. Frontend navigates to detail page. User sees their existing setup.
- **Selecting existing security without config:** Creates disabled config. 200 response. Frontend navigates to detail page.
- **Creating new security — ISIN collision:** 409 with existing security details. Frontend shows error with link to select the existing one instead.
- **Creating new security — MIC:TICKER collision:** 409 with existing security details.
- **Creating new security — ticker collision (different MIC):** Allowed; multiple `security_master` docs in same partition is by design.

### 6.5 Frontend Post-Success

On successful response:
1. Display success toast ("AAPL added" or "AAPL already in watchlist").
2. Navigate to `/symbols/{MIC}:{TICKER}`.
3. Page shows correct `symbol_state` with all agents disabled (if new) or previous state (if existing).

### 6.6 Impact on Existing `POST /api/symbols`

The old endpoint (`POST /api/symbols` in `app.py`) continues to work unchanged. It creates a `symbol_config` directly with user-specified agent flags (for backward compatibility and for any other callers). However, the frontend `AddSymbolForm` **no longer calls it** — it calls `POST /api/symbols/add` instead. The old endpoint is not deprecated in this phase but may be in a future phase.

---

## 7. `total_shares` Reconciliation — Report Only

### 7.1 Endpoint

```
GET /api/admin/total-shares-reconciliation
```

### 7.2 Response

```json
{
  "reconciliation": [
    {
      "ticker": "AAPL",
      "security_id": "XNYS:AAPL",
      "config_total_shares": 100,
      "portfolio_derived_shares": "150.000000",
      "delta": "50.000000",
      "status": "mismatch"
    },
    {
      "ticker": "MSFT",
      "security_id": "XNYS:MSFT",
      "config_total_shares": 200,
      "portfolio_derived_shares": "200.000000",
      "delta": "0",
      "status": "match"
    }
  ],
  "summary": {
    "total_symbols": 15,
    "matched": 12,
    "mismatched": 3,
    "no_portfolio_data": 0
  }
}
```

### 7.3 Scope

- Compares `symbol_config.total_shares` against portfolio-derived `SUM(BUY) - SUM(SELL) + SUM(share_legs)` per ticker.
- **This phase: report only.** No automatic overwrite. No read-only cutover of `total_shares`.
- The PUT `/api/symbols/{symbol}` endpoint continues accepting `total_shares` updates.
- Future phase will deprecate `total_shares` writes and source exclusively from portfolio ledger.

---

## 8. Tests and Rollout Sequence

### 8.1 Test Plan

| Test File | Owner | Scope | Count (est.) |
|-----------|-------|-------|--------------|
| `test_ensure_symbol_config.py` | Basher | Idempotency, disabled defaults, race condition (409→re-read), existing no-op, security_id field, collision warning | ~15 |
| `test_symbol_config_triggers.py` | Basher | Import commit triggers ensure, manual movement triggers, transfer-in triggers, failure isolation (ledger not rolled back) | ~12 |
| `test_backfill_endpoints.py` | Basher | Dry-run accuracy, confirmed execution, collision warnings, existing config safety | ~10 |
| `test_unified_symbol_detail.py` | Basher | Response shape by state (watchlist_only, portfolio_only, etc.), legacy route resolution, MIC collision 300 response, portfolio section population | ~15 |
| `test_symbols_overview_sections.py` | Basher | Two-list classification (portfolio vs watchlist), mutual exclusivity, portfolio precedence, portfolio-derived fields, backward-compat `rows` array, edge cases (soft-deleted, zero-share, migration) | ~12 |
| `test_unified_add_symbol.py` | Basher | Add via select-existing (with/without config), add via create-new, ISIN collision, MIC:TICKER collision, config_warning on partial failure, search endpoint | ~12 |
| `test_total_shares_reconciliation.py` | Basher | Report accuracy, match/mismatch detection, no-portfolio-data handling | ~5 |
| `test_read_repair_holdings.py` | Basher | Holdings computation triggers ensure for missing configs | ~5 |

**Estimated total: ~86 tests** (additive to existing 687).

### 8.2 Rollout Sequence

| Phase | Scope | Dependencies |
|-------|-------|--------------|
| **R1: Core function** | `ensure_symbol_config` + unit tests | None |
| **R2: Triggers** | Wire ensure into import commit, manual movement, transfer-in + integration tests | R1 |
| **R3: Backfill** | Admin endpoints + dry-run verification on production data | R1 |
| **R4: Unified detail API** | Extended `/api/symbols/{symbol}/detail` response + resolution logic + tests | R1, R3 (backfill populates security_id) |
| **R5: Frontend unified detail** | Conditional sections, disambiguation page, new Portfolio card/movements table | R4 |
| **R5b: Symbols page two-list layout** | Split `SymbolsTable` into Portfolio/Watchlist sections; extend overview API with `portfolio_rows`/`watchlist_rows` | R4, R5 |
| **R6: Unified Add Symbol** | `POST /api/symbols/add`, `GET /api/securities/search`, replace `AddSymbolForm` frontend | R1 |
| **R7: Reconciliation report** | `total_shares` comparison endpoint | R4 |
| **R8: Read-repair** | Holdings-triggered ensure + tests | R1, R2 |

**Suggested implementation order:** R1 → R2 → R3 → R6 → R4 → R5 → R5b → R7 → R8  
(R6 is independent after R1; R5b follows R5; R7/R8 can happen in parallel with R5b.)

---

## 9. File Ownership and Agent Assignments

### Livingston (Backend)

| File | Action | Phase |
|------|--------|-------|
| `backend/src/portfolio/symbol_config_sync.py` | **NEW** — `ensure_symbol_config` function | R1 |
| `backend/src/portfolio/import_service.py` | **EDIT** — add ensure calls post-commit | R2 |
| `backend/src/portfolio/cosmos_portfolio.py` | **EDIT** — add ensure call in `create_manual_movement`, `create_transfer_pair` | R2 |
| `backend/src/portfolio/holdings_service.py` | **EDIT** — add read-repair ensure in `compute_holdings` | R8 |
| `backend/web/portfolio_routes.py` | **EDIT** — add `POST /api/symbols/add`, `GET /api/securities/search`, `GET/POST /api/admin/symbol-config-backfill`, `GET /api/admin/total-shares-reconciliation` | R3, R6, R7 |
| `backend/web/app.py` | **EDIT** — extend `_compute_symbol_detail` with security/portfolio/symbol_state fields; add MIC:TICKER resolution logic; extend `_compute_symbols_overview` with two-list classification (`portfolio_rows`/`watchlist_rows`) and portfolio-derived fields | R4, R5b |

### Rusty (Frontend)

| File | Action | Phase |
|------|--------|-------|
| `frontend/src/app/symbols/[symbol]/page.tsx` | **EDIT** — add Portfolio Holdings card, Recent Movements, conditional sections, disambiguation | R5 |
| `frontend/src/components/PortfolioHoldingsCard.tsx` | **NEW** — compact holdings display for symbol detail | R5 |
| `frontend/src/components/SymbolMovementsTable.tsx` | **NEW** — recent movements mini-table for symbol detail | R5 |
| `frontend/src/components/SymbolDisambiguation.tsx` | **NEW** — 300 Multiple Choices renderer | R5 |
| `frontend/src/app/symbols/page.tsx` | **EDIT** — two-section layout (Portfolio / Watchlist headings with counts); use new unified `AddSymbolForm`; consume `portfolio_rows`/`watchlist_rows` from API | R5b, R6 |
| `frontend/src/components/SymbolsTable.tsx` | **EDIT** — accept `listSection` prop ("portfolio" \| "watchlist"); show portfolio-derived columns (Shares, Avg Cost, Invested) in portfolio mode; hide them in watchlist mode | R5b |
| `frontend/src/types/symbols.ts` | **EDIT** — extend `SymbolRow` with `list_section`, `security_id`, `portfolio_shares`, `portfolio_avg_cost_eur`, `portfolio_invested_eur`; extend `SymbolsOverview` with `portfolio_rows`, `watchlist_rows`, `portfolio_count`, `watchlist_count` | R5b |
| `frontend/src/components/AddSymbolForm.tsx` | **REWRITE** — search-first SecurityMaster select/create flow; no agent toggles; calls `POST /api/symbols/add`; reuses `SecurityCreateForm` field layout for inline creation | R6 |
| `frontend/src/types/symbol-detail.ts` | **EDIT** — extend `SymbolDetail` type with security/portfolio/symbol_state | R5 |
| `frontend/src/lib/portfolio-api.ts` | **EDIT** — add `addSymbol` and `searchSecurities` functions | R6 |

### Basher (Tests)

| File | Phase |
|------|-------|
| `backend/tests/test_ensure_symbol_config.py` | R1 |
| `backend/tests/test_symbol_config_triggers.py` | R2 |
| `backend/tests/test_backfill_endpoints.py` | R3 |
| `backend/tests/test_unified_symbol_detail.py` | R4 |
| `backend/tests/test_symbols_overview_sections.py` | R5b |
| `backend/tests/test_unified_add_symbol.py` | R6 |
| `backend/tests/test_total_shares_reconciliation.py` | R7 |
| `backend/tests/test_read_repair_holdings.py` | R8 |

### Danny (Architecture / Review)

- Gate review at R1 completion (core function correctness).
- Gate review at R4 completion (unified detail contract parity).
- Gate review at R5 completion (frontend conditional behavior correctness).
- Gate review at R5b completion (two-list mutual exclusivity, no duplication).
- Final integration review before production deploy.

---

## Acceptance Criteria

| # | Criterion | Verified by |
|---|-----------|-------------|
| AC-1 | `ensure_symbol_config` is idempotent: calling twice returns same config, never overwrites | `test_ensure_symbol_config` |
| AC-2 | New auto-created configs have ALL agents, alerts, notifications, and automation disabled | `test_ensure_symbol_config` |
| AC-3 | Import commit, manual movement, and transfer-in all trigger ensure after ledger write; ensure failure never rolls back ledger | `test_symbol_config_triggers` |
| AC-4 | Backfill dry-run reports exact gap count and collision warnings; confirmed execution creates only missing configs | `test_backfill_endpoints` |
| AC-5 | `/api/symbols/{symbol}/detail` returns `security`, `portfolio`, and `symbol_state` fields; legacy ticker routes resolve unambiguously or return 300 | `test_unified_symbol_detail` |
| AC-6 | Frontend shows Portfolio Holdings card only when ledger history exists; shows "0 shares — historical" for zero-share positions | Manual + visual review |
| AC-7 | Unified "Add Symbol" creates or selects SecurityMaster + ensures disabled symbol_config; navigates to `/symbols/{MIC}:{TICKER}` | `test_unified_add_symbol` |
| AC-8 | "Add Symbol" selecting an existing security with existing config does NOT modify that config (no-op on ensure) | `test_unified_add_symbol` |
| AC-8b | ISIN/MIC:TICKER collisions on create-new return 409 with existing security details | `test_unified_add_symbol` |
| AC-9 | `total_shares` reconciliation report correctly identifies matches and mismatches; no automatic writes | `test_total_shares_reconciliation` |
| AC-10 | Watchlist-only symbols continue to work unchanged (no portfolio section, all existing features intact) | `test_unified_symbol_detail` |
| AC-11 | Holdings read-repair creates missing configs transparently | `test_read_repair_holdings` |
| AC-12 | All existing 687 tests continue to pass after implementation | CI pipeline |
| AC-13 | MIC:TICKER is canonical identity; `security_id` field propagated consistently | All test files |
| AC-14 | `/api/symbols/overview` returns `portfolio_rows` and `watchlist_rows` as mutually exclusive arrays; no symbol appears in both | `test_symbols_overview_sections` |
| AC-15 | Portfolio classification takes precedence: any symbol with ledger history (including soft-deleted, zero-share) is in `portfolio_rows`, never in `watchlist_rows` | `test_symbols_overview_sections` |
| AC-16 | `portfolio_rows` entries carry `portfolio_shares`, `portfolio_avg_cost_eur`, `portfolio_invested_eur` derived from holdings; `watchlist_rows` entries have these as `null` | `test_symbols_overview_sections` |
| AC-17 | Frontend renders two collapsible sections (Portfolio / Watchlist) with independent sort/filter; symbol never duplicated across sections | Manual + visual review |
| AC-18 | Backward-compat `rows` array remains as flat union of both sections | `test_symbols_overview_sections` |

---

## Invariants (Do NOT Violate)

1. **Ledger writes are authoritative.** `ensure_symbol_config` failure NEVER causes ledger rollback.
2. **Existing configs are sacred.** `ensure_symbol_config` NEVER modifies an existing `symbol_config`.
3. **All auto-created configs are fully disabled.** User must explicitly enable any agent or notification.
4. **Zero-share positions remain in config.** No auto-removal, no auto-disable.
5. **Watchlist-only symbols remain first-class.** No degradation of current functionality.
6. **`total_shares` remains writable this phase.** Report-only reconciliation; read-only cutover is future work.
7. **Cross-container writes are best-effort.** `symbols` and `portfolio` are separate Cosmos containers; no transactional guarantee between them. Design relies on idempotent retry + read-repair.
8. **One Add Symbol experience.** There is exactly ONE user-facing flow for adding symbols. It always goes through SecurityMaster (create or select) and then `ensure_symbol_config`. No separate "Add Security" button or parallel UX exists.
9. **Two-list mutual exclusivity.** On the Symbols page, every `symbol_config` appears in exactly one section: Portfolio (if any ledger history exists) or Watchlist (if none). Portfolio takes display precedence. A symbol never appears in both lists.
