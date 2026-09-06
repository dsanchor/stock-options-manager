# Provider-Specific Symbols for Security Creation During Import

**Date:** 2026-09-06
**Author:** Danny (Lead)
**Status:** FROZEN — implementation-ready
**Scope:** Security catalog schema + creation flows only; no yfinance consumer refactoring
**Depends on:** `danny-portfolio-implementation-contract.md` (contract v1.1)

---

## Problem

The canonical `security_id` uses `MIC:TICKER` (e.g. `XMAD:ENG`), but yfinance requires exchange-specific suffixes (e.g. `ENG.MC`). Securities created during portfolio import have no provider symbol, so downstream data fetches fail for non-US exchanges. We need a structured place to store provider-specific symbols at creation time without changing the canonical identity model.

---

## Decision Summary

1. Add an **optional `provider_symbols` map** to `security_master` documents.
2. During security creation (standalone and inline import), **auto-suggest** the yfinance symbol from the MIC using a known suffix table.
3. The suggestion is **editable and optional**; explicit user input always wins.
4. Unknown MICs produce **no suggestion** (field left empty, not fabricated).
5. Existing securities without `provider_symbols` continue to work unchanged.
6. No automatic activation of watchlists/agents; no change to `security_id`.

---

## §1 Schema: `provider_symbols` Field

### 1.1 Document Shape (Cosmos `security_master`)

```jsonc
{
  "id": "sec_XMAD_ENG",
  "symbol": "ENG",
  "doc_type": "security_master",
  "security_id": "XMAD:ENG",
  "ticker": "ENG",
  "company_name": "Enagás S.A.",
  "exchange_mic": "XMAD",
  // ... existing fields unchanged ...
  "provider_symbols": {          // NEW — optional map
    "yfinance": "ENG.MC"         // provider → symbol string
  }
}
```

### 1.2 Shape Rules

| Property | Type | Required | Constraint |
|----------|------|----------|------------|
| `provider_symbols` | `Dict[str, str]` or `null` | No | Optional map; absent or `null` for legacy docs |
| Key | `str` | — | Provider identifier; Phase 1 only recognises `"yfinance"` |
| Value | `str` | — | 1–30 chars; `[A-Za-z0-9._^-]`; no whitespace; case-preserved |

### 1.3 Rationale: Map vs Dedicated Field

A generic `provider_symbols` map (not a single `yfinance_symbol` string) allows future providers (e.g. `"bloomberg"`, `"refinitiv"`) without schema migration. The map is flat, JSON-serializable, and cheap to query.

---

## §2 yfinance Suffix Mapping Table

The following table maps ISO 10383 MIC codes to yfinance ticker suffixes. This table lives as a constant in the backend (`backend/src/portfolio/provider_symbols.py`) and is mirrored as a read-only lookup in the frontend.

| MIC | Exchange | yfinance Suffix | Example: Ticker `SAN` |
|------|----------|----------------|-----------------------|
| `XMAD` | BME (Madrid) | `.MC` | `SAN.MC` |
| `XAMS` | Euronext Amsterdam | `.AS` | `SAN.AS` |
| `XLON` | London Stock Exchange | `.L` | `SAN.L` |
| `XPAR` | Euronext Paris | `.PA` | `SAN.PA` |
| `XETR` | Deutsche Börse (Xetra) | `.DE` | `SAN.DE` |
| `XSWX` | SIX Swiss Exchange | `.SW` | `SAN.SW` |
| `XBRU` | Euronext Brussels | `.BR` | `SAN.BR` |
| `XLIS` | Euronext Lisbon | `.LS` | `SAN.LS` |
| `XNYS` | NYSE | _(none)_ | `SAN` |
| `XNAS` | NASDAQ | _(none)_ | `SAN` |

### 2.1 Suggestion Formula

```python
def suggest_yfinance_symbol(ticker: str, exchange_mic: str) -> str | None:
    """Return suggested yfinance symbol, or None for unknown MIC."""
    suffix = MIC_TO_YFINANCE_SUFFIX.get(exchange_mic.upper())
    if suffix is None:
        return None          # unknown MIC → no fabrication
    return f"{ticker.upper()}{suffix}"
```

- For US exchanges (`XNYS`, `XNAS`): suffix is `""` → symbol equals ticker.
- For unknown MICs not in the table: returns `None`. The UI shows an empty field, not a guess.

### 2.2 Extensibility

The table is a simple dict constant. Adding a new exchange is a one-line addition; no schema change or migration required.

---

## §3 API Changes

### 3.1 `POST /api/securities` — Create Security

**Request body** — add optional field:

```jsonc
{
  "ticker": "ENG",
  "company_name": "Enagás S.A.",
  "exchange_mic": "XMAD",
  "listing_currency": "EUR",
  // existing optional fields: isin, cusip, sedol, aliases, broker_ids
  "provider_symbols": {          // NEW — optional
    "yfinance": "ENG.MC"
  }
}
```

**Backend behavior:**
1. If `provider_symbols` is provided and non-empty, validate each value (see §5).
2. If `provider_symbols` is omitted or `null`, store nothing (field absent from doc).
3. The backend does NOT auto-populate `provider_symbols` on the server side. The suggestion is computed client-side (or via a new suggestion helper route — see §3.3). The user's explicit input is stored as-is.

**Response** (201): includes `provider_symbols` if present.

### 3.2 `POST /api/import/sessions/{session_id}/securities` — Inline Create

Same schema extension. The `security_data` body in inline creation accepts `provider_symbols` identically to standalone `POST /api/securities`. The `SecurityCreateForm` component sends it in both paths.

### 3.3 `GET /api/securities/suggest-provider-symbol?ticker=ENG&mic=XMAD` (Optional Helper)

Returns:
```json
{ "yfinance": "ENG.MC" }
```
or `{}` for unknown MIC. This is a stateless, zero-cost endpoint. If the frontend embeds the suffix table locally, this endpoint is not required for Phase 1 but is recommended for DRY.

### 3.4 `GET /api/securities` and `GET /api/securities/{id}` — Response Shape

Include `provider_symbols` in the response when present:

```jsonc
{
  "security_id": "XMAD:ENG",
  "ticker": "ENG",
  // ...
  "provider_symbols": { "yfinance": "ENG.MC" }   // included if present
}
```

Existing securities without `provider_symbols` return without the field (not `null`, just absent).

---

## §4 Pydantic Model Changes (Backend)

### 4.1 `SecurityMasterCreate` — add optional field

```python
class SecurityMasterCreate(BaseModel):
    # ... existing fields ...
    provider_symbols: Optional[Dict[str, str]] = None
```

### 4.2 `SecurityMasterDoc` — add optional field

```python
class SecurityMasterDoc(BaseModel):
    # ... existing fields ...
    provider_symbols: Optional[Dict[str, str]] = None
```

### 4.3 `CosmosSecuritiesService.create_security` — persist the field

In the `create_security` method, after building the doc dict, add:

```python
if data.get("provider_symbols"):
    doc["provider_symbols"] = data["provider_symbols"]
```

Same treatment as `isin`, `cusip`, `sedol`, `broker_ids` — optional, only stored when present.

---

## §5 Validation Rules

### 5.1 `provider_symbols` Value Constraints

| Rule | Constraint |
|------|-----------|
| Max keys | 10 (future-proof; Phase 1 only has `yfinance`) |
| Key format | `[a-z][a-z0-9_]{0,29}` (lowercase, no dots/hyphens) |
| Value format | 1–30 chars, allowed: `[A-Za-z0-9._^-]` |
| Value trimming | Strip leading/trailing whitespace before validation |
| Empty value | Treated as absent; key removed from map |

### 5.2 Why Not Stricter?

Yahoo Finance uses suffixes like `.MC`, `.L`, class shares like `BRK-B`, warrants with `.W`, preferred with `-P`. The regex `[A-Za-z0-9._^-]` covers all known patterns without over-restricting.

### 5.3 Backend Validation Function

```python
import re

_PROVIDER_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,29}$")
_PROVIDER_VALUE_RE = re.compile(r"^[A-Za-z0-9._^\-]{1,30}$")

def validate_provider_symbols(ps: dict) -> dict:
    """Validate and normalize provider_symbols map. Returns cleaned map."""
    if not ps or not isinstance(ps, dict):
        return {}
    if len(ps) > 10:
        raise ValueError("provider_symbols: max 10 entries")
    cleaned = {}
    for k, v in ps.items():
        if not _PROVIDER_KEY_RE.match(k):
            raise ValueError(f"provider_symbols: invalid key '{k}'")
        v = str(v).strip()
        if not v:
            continue  # empty → omit
        if not _PROVIDER_VALUE_RE.match(v):
            raise ValueError(f"provider_symbols[{k}]: invalid value '{v}'")
        cleaned[k] = v
    return cleaned
```

---

## §6 TypeScript Type Changes (Frontend)

### 6.1 `types/portfolio.ts`

```typescript
export interface SecurityMaster {
  // ... existing fields ...
  provider_symbols?: Record<string, string>;   // NEW
}

export interface CreateSecurityRequest {
  // ... existing fields ...
  provider_symbols?: Record<string, string>;   // NEW
}
```

### 6.2 Suffix Table (Frontend)

```typescript
// lib/provider-symbols.ts
export const MIC_TO_YFINANCE_SUFFIX: Record<string, string> = {
  XMAD: ".MC",
  XAMS: ".AS",
  XLON: ".L",
  XPAR: ".PA",
  XETR: ".DE",
  XSWX: ".SW",
  XBRU: ".BR",
  XLIS: ".LS",
  XNYS: "",
  XNAS: "",
};

export function suggestYfinanceSymbol(
  ticker: string,
  mic: string,
): string | null {
  const suffix = MIC_TO_YFINANCE_SUFFIX[mic.toUpperCase()];
  if (suffix === undefined) return null;
  return `${ticker.toUpperCase()}${suffix}`;
}
```

---

## §7 SecurityCreateForm Changes

### 7.1 New Field in Optional Identifiers Section

The existing `SecurityCreateForm` has a collapsible "Add ISIN / CUSIP / SEDOL" section. Add a **yfinance symbol** field to this section (or visible by default — implementer's choice given it's the most commonly needed identifier).

**Behavior:**
1. When user types/changes `ticker` or `mic`, auto-compute suggestion via `suggestYfinanceSymbol(ticker, mic)`.
2. Display the suggestion as a **pre-filled but editable** input.
3. If the user clears the field, `provider_symbols.yfinance` is omitted from the request.
4. If the MIC is unknown, the field shows empty with placeholder "e.g. ENG.MC".
5. The suggestion updates reactively but **never overwrites** a value the user has manually edited. Track a `userEdited` flag per field.

### 7.2 Form State Addition

```typescript
const [yfinanceSymbol, setYfinanceSymbol] = useState("");
const [yfinanceUserEdited, setYfinanceUserEdited] = useState(false);

// Effect: auto-suggest when ticker/mic change, unless user manually edited
useEffect(() => {
  if (!yfinanceUserEdited) {
    const suggestion = suggestYfinanceSymbol(ticker, mic);
    setYfinanceSymbol(suggestion ?? "");
  }
}, [ticker, mic, yfinanceUserEdited]);
```

### 7.3 Payload Construction

```typescript
if (yfinanceSymbol.trim()) {
  payload.provider_symbols = { yfinance: yfinanceSymbol.trim() };
}
```

---

## §8 Backward Compatibility

| Scenario | Behavior |
|----------|----------|
| Existing `security_master` docs without `provider_symbols` | Field simply absent; no migration needed |
| `GET /api/securities` response for old docs | `provider_symbols` key omitted (not `null`) |
| `POST /api/securities` without `provider_symbols` | Works exactly as today; field not stored |
| yfinance consumers reading `provider_symbols` | **Not changed in this contract**; consumers continue using bare ticker. A future contract will wire consumers to prefer `provider_symbols.yfinance` when present. |
| `security_id` format | **Unchanged**: `MIC:TICKER` remains canonical everywhere |
| Watchlists/agents | **Not activated** by security creation; no behavior change |

---

## §9 What This Contract Does NOT Cover

- ❌ Refactoring `YFinanceDataProvider.fetch_all(symbol)` to read `provider_symbols.yfinance` — separate contract
- ❌ Auto-populating `provider_symbols` for existing securities (backfill) — separate task
- ❌ Provider symbol validation against live yfinance (no network call during create)
- ❌ Multiple yfinance symbols per security (one-to-one mapping)
- ❌ Display of `provider_symbols` in Holdings/Movements views

---

## §10 Test Acceptance Criteria

### 10.1 Backend Tests (`backend/tests/`)

| ID | Test | Assertion |
|----|------|-----------|
| PS-B1 | `POST /api/securities` with `provider_symbols: {"yfinance": "ENG.MC"}` | 201; response includes `provider_symbols` |
| PS-B2 | `POST /api/securities` without `provider_symbols` | 201; response has no `provider_symbols` key |
| PS-B3 | `GET /api/securities/{id}` for doc with `provider_symbols` | Response includes the map |
| PS-B4 | `GET /api/securities/{id}` for doc without `provider_symbols` | Response has no `provider_symbols` key (not null) |
| PS-B5 | `GET /api/securities` list includes `provider_symbols` when present | Verify in list serialization |
| PS-B6 | Invalid provider key `"Yahoo!"` | 400 validation error |
| PS-B7 | Invalid value `"ENG MC"` (space) | 400 validation error |
| PS-B8 | Value exceeds 30 chars | 400 validation error |
| PS-B9 | Empty value `{"yfinance": ""}` | Stored without the key (cleaned) |
| PS-B10 | `suggest_yfinance_symbol("ENG", "XMAD")` | Returns `"ENG.MC"` |
| PS-B11 | `suggest_yfinance_symbol("AAPL", "XNYS")` | Returns `"AAPL"` |
| PS-B12 | `suggest_yfinance_symbol("FOO", "XZZZ")` | Returns `None` |
| PS-B13 | Inline create `POST /api/import/sessions/{sid}/securities` with `provider_symbols` | Security doc persisted with `provider_symbols` |

### 10.2 Frontend Tests (if applicable)

| ID | Test | Assertion |
|----|------|-----------|
| PS-F1 | `suggestYfinanceSymbol("ENG", "XMAD")` | Returns `"ENG.MC"` |
| PS-F2 | `suggestYfinanceSymbol("AAPL", "XNYS")` | Returns `"AAPL"` |
| PS-F3 | `suggestYfinanceSymbol("FOO", "XZZZ")` | Returns `null` |
| PS-F4 | SecurityCreateForm: typing MIC `XMAD` auto-fills yfinance field | Field shows `{TICKER}.MC` |
| PS-F5 | SecurityCreateForm: user edits yfinance field, then changes MIC | User value preserved (not overwritten) |
| PS-F6 | SecurityCreateForm: user clears yfinance field | `provider_symbols` omitted from payload |

---

## §11 File Inventory (New & Modified)

### Backend

| File | Action | Description |
|------|--------|-------------|
| `backend/src/portfolio/provider_symbols.py` | **NEW** | `MIC_TO_YFINANCE_SUFFIX` dict, `suggest_yfinance_symbol()`, `validate_provider_symbols()` |
| `backend/src/portfolio/models.py` | MODIFY | Add `provider_symbols` to `SecurityMasterCreate` and `SecurityMasterDoc` |
| `backend/src/portfolio/cosmos_securities.py` | MODIFY | Persist `provider_symbols` in `create_security`; include in response serialization |
| `backend/web/portfolio_routes.py` | MODIFY | Validate `provider_symbols` in `POST /api/securities` and inline create; include in GET responses |
| `backend/tests/test_portfolio_endpoints.py` | MODIFY | Add PS-B1 through PS-B9, PS-B13 |
| `backend/tests/test_provider_symbols.py` | **NEW** | Unit tests for PS-B10 through PS-B12, validation edge cases |

### Frontend

| File | Action | Description |
|------|--------|-------------|
| `frontend/src/lib/provider-symbols.ts` | **NEW** | `MIC_TO_YFINANCE_SUFFIX`, `suggestYfinanceSymbol()` |
| `frontend/src/types/portfolio.ts` | MODIFY | Add `provider_symbols` to `SecurityMaster` and `CreateSecurityRequest` |
| `frontend/src/components/SecurityCreateForm.tsx` | MODIFY | Add yfinance symbol field with auto-suggest |

---

## §12 Agent Assignments

| Task | Owner | Depends On |
|------|-------|------------|
| `provider_symbols.py` (suffix table + validation) | **Livingston** (Backend) | — |
| Pydantic model updates (`models.py`) | **Livingston** (Backend) | — |
| `cosmos_securities.py` persistence | **Livingston** (Backend) | `provider_symbols.py` |
| Route updates (`portfolio_routes.py`) | **Livingston** (Backend) | `cosmos_securities.py`, `provider_symbols.py` |
| Backend tests | **Livingston** (Backend) | All backend changes |
| `provider-symbols.ts` (frontend suffix table) | **Rusty** (Frontend) | — |
| Type updates (`portfolio.ts`) | **Rusty** (Frontend) | — |
| `SecurityCreateForm.tsx` UI changes | **Rusty** (Frontend) | `provider-symbols.ts`, types |
| Frontend tests (if applicable) | **Rusty** (Frontend) | All frontend changes |

**Parallel execution:** Livingston and Rusty can work simultaneously — backend and frontend changes are independent. The suffix table is duplicated (backend + frontend) intentionally; they must stay in sync but are owned by their respective agents.

---

## §13 Migration & Rollout

- **No data migration required.** Existing documents without `provider_symbols` are fully compatible.
- **No feature flag needed.** The field is optional and additive.
- **Future consumer wiring:** A separate contract will update `YFinanceDataProvider` and `YFinanceFetcher` to prefer `provider_symbols.yfinance` when available, falling back to bare ticker. That change is explicitly out of scope here.

---

## §14 Implementation History

### Rusty — Frontend (2026-09-06)

**Files changed:**
- `frontend/src/lib/provider-symbols.ts` — NEW: `MIC_TO_YFINANCE_SUFFIX` map + `suggestYfinanceSymbol()` pure helper
- `frontend/src/types/portfolio.ts` — Added `provider_symbols?: Record<string, string>` to `SecurityMaster` and `CreateSecurityRequest`
- `frontend/src/components/SecurityCreateForm.tsx` — Added yfinance symbol field in optional identifiers section with auto-suggest + `userEdited` guard

**Key decisions:**
- No test runner (vitest/jest absent); validated via `tsc --noEmit` (exit 0)
- `yfinanceUserEdited` flag set on first keystroke; subsequent ticker/MIC changes do not overwrite manual edits
- `provider_symbols` omitted from payload when field is empty (not sent as `null`)
- UI explanation placed inline below the field label; optional section toggle relabelled to mention Yahoo Finance symbol
