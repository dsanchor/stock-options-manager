# Unified Design: Portfolio Ledger, Securities Master, and Conversational Imports

**Date:** 2026-09-05  
**Consolidated by:** Scribe  
**Authoritative sources:**
- `danny-security-master-final-resolution.md` — container & identity resolution (Danny, Architecture)
- `danny-chat-import-architecture.md` — conversational chat replacing wizard (Danny, Architecture)
- `livingston-chat-import-state.md` — session state machine & persistence (Livingston, Persistence)
- `livingston-security-identity-import-matching.md` — security master catalog & inline creation (Livingston)
- `rusty-chat-import-ux.md` — frontend UX & component reuse (Rusty, Frontend)
- `copilot-directive-20260905T172036+0200.md`, `copilot-directive-20260905T172200+0200.md` — user directives

**Status:** CONSOLIDATED — consolidation of all applicable inbox designs, deduplicated and maintainable. Implementation-ready.

---

## Container Architecture — Unified View

### Definitive Container Inventory

| Container | Partition Key | Purpose | TTL | Doc Types |
|-----------|---------------|---------|-----|-----------|
| `symbols` | `/symbol` (ticker) | **NEW:** Identity catalog with `security_master` docs; existing `symbol_config`, activity, alerts | None | `security_master` (NEW), `symbol_config`, `activity`, `alert`, `report` |
| `portfolio` | `/account_id` | **NEW:** Transactional ledger (movements), account profiles, staged import rows | `-1` (doc-controlled) | `ledger_txn`, `ca_event`, `ca_leg`, `account`, `staged_import_row` (90-day TTL), `import_batch` summary |
| `import_sessions` | `/session_id` | **NEW:** Conversational import state machine, questions, LLM call records | `-1` (doc-controlled) | `import_session` (7-day TTL), `import_question`, `llm_call_record` |
| `telemetry`, `settings`, `dgi_screener`, `calendar`, `agent_traces` | (existing) | (existing) | (existing) | (existing) |

**Key changes from prior design:**
- ❌ `portfolio_ledger` (old name) → ✅ `portfolio` (normalized)
- ❌ `portfolio._global` security storage → ✅ `symbols.{ticker}` with `security_master` documents
- ❌ Sessions in `portfolio._global` → ✅ Dedicated `import_sessions` container with clean TTL isolation

### Security Master: Canonical Identity in `symbols` Container

**Location:** `symbols` container, partitioned by ticker (`/symbol`)  
**Document type:** `security_master`  
**Document ID format:** `sec_MIC_TICKER` (colons replaced with underscores)  
**Security ID format:** `MIC:TICKER` (MIC first, ticker second — standard namespace convention)

```json
{
  "id": "sec_XNYS_AAPL",
  "symbol": "AAPL",
  "doc_type": "security_master",
  "security_id": "XNYS:AAPL",
  "legacy_symbol": "AAPL",
  "isin": "US0378331005",
  "exchange_mic": "XNYS",
  "exchange_name": "NASDAQ OMX BX",
  "ticker": "AAPL",
  "company_name": "Apple Inc.",
  "asset_class": "Equity",
  "listing_currency": "USD",
  "cusip": "037833100",
  "sedol": "2046251",
  "broker_ids": { "ibkr_conid": "265598", "heytrade_id": "..." },
  "aliases": [
    { "source": "yfinance", "value": "APPLE INC", "normalized": "apple inc" },
    { "source": "user", "value": "Apple", "normalized": "apple" }
  ],
  "status": "ACTIVE",
  "created_at": "2026-09-05T...",
  "updated_at": "2026-09-05T..."
}
```

**Co-location with existing `symbol_config` in same partition:**
```
symbols partition "AAPL":
├─ id: "config_AAPL" (doc_type: symbol_config) — existing, unchanged
├─ id: "sec_XNYS_AAPL" (doc_type: security_master) — NEW
└─ other activity/alert docs
```

**Ticker collision handling:**
```
symbols partition "SAN":
├─ id: "config_SAN" (doc_type: symbol_config) — Santander US (if present)
├─ id: "sec_XMAD_SAN" (doc_type: security_master) — Santander Madrid
└─ id: "sec_XPAR_SAN" (doc_type: security_master) — Sanofi Paris
```

When a bare ticker has multiple `security_master` docs, the `symbol_config.security_id` field (new in Phase M2) disambiguates. If no `symbol_config` exists (portfolio-only), a 300 Multiple Choices response lists the options.

### Portfolio Container: Ledger-First, Account-Partitioned

**Partition key:** `/account_id`  
**Document types:**
- `ledger_txn` — Individual movements (BUY, SELL, DIVIDEND)
- `ca_event` — Corporate action parent (DIVIDEND with share leg)
- `ca_leg` — Corporate action sub-leg (share portion of mixed dividend)
- `account` — Account/broker profile
- `staged_import_row` — Temporary parsed row (90-day TTL), waiting for commit
- `import_batch` — Post-commit summary (lightweight)

**TTL implications:** Container default `ttl = -1` (enabled, documents control expiry). Only `staged_import_row` documents set `ttl` values (90 days). Permanent ledger records do not set `ttl` — they persist forever.

### Import Sessions Container: Dedicated, TTL-Isolated

**Partition key:** `/session_id`  
**Document types:**
- `import_session` — Conversation state machine (7-day TTL)
- `import_question` — Individual questions in conversation
- `llm_call_record` — LLM interaction history (provenance)

**Isolation rationale:**
- Prevents TTL-enabled container from accidentally expiring ledger records
- Uniform distribution (each session is its own partition)
- Light indexing (state, created_by, expires_at) distinct from ledger indexes
- Clean lifecycle separation (7-day vs. permanent)

---

## Conversational Import — Architecture & State Machine

### Fundamental Safety Invariant

**The LLM never parses financial amounts, never computes arithmetic, never writes to the ledger directly, and never auto-confirms fuzzy security identity.**

The LLM is an orchestration layer presenting structured metadata and user-facing questions. Deterministic code performs:
- File parsing (delimiters, locales, encodings)
- Number parsing (Spanish comma/period normalization)
- Date parsing
- Arithmetic validation (gross − fees − withholding = net)
- Deduplication (row hashes, fingerprints)
- Security alias resolution (exact match)
- Ledger writes (only after explicit user confirmation)

### Session State Machine

```
CREATED ──► FILE_PARSED ──► BATCH_Q ──► ENTITY_Q ──► GROUP_Q ──► ROW_Q ──► VALIDATING ──► PREVIEW_READY ──► COMMIT_CONFIRMED ──► COMMITTED
                                          ▲_______answer changed____________│
                                                                    (blocking
                                                                    issues)
```

| State | Entry | Exit | Key constraint |
|-------|-------|------|-----------------|
| `CREATED` | File received | Parse completes | Initial container write |
| `FILE_PARSED` | Parse completes | BATCH/ENTITY questions generated | Rows extracted, fingerprints computed |
| `BATCH_QUESTIONS` | BATCH question exists | All BATCH answers recorded | File-level setup (currency, account, import type) |
| `ENTITY_QUESTIONS` | BATCH complete | All ENTITY answers → security resolved | Company-to-security mapping, one per normalized name |
| `ROW_GROUP_QUESTIONS` | ENTITY complete | Shared ambiguities resolved | Classification questions per fingerprint |
| `ROW_QUESTIONS` | User breaks group | All per-row exceptions answered | Rare; user opted to handle rows individually |
| `VALIDATING` | Any state change or answer update | Validation pass completes | Deterministic revalidation, LLM not re-called unless new entity |
| `PREVIEW_READY` | Validation clean | User confirms | All questions answered, no blocking issues |
| `COMMIT_CONFIRMED` | Confirm clicked | Ledger writes complete | No further UI interaction until COMMITTED |
| `COMMITTED` | All writes succeed | — (terminal) | Immutable; ledger records created, session expires in 7 days |
| `EXPIRED` | TTL elapsed (any pre-COMMITTED state) | — (terminal) | Session deleted; no ledger written; idempotent if import re-uploaded |

### Question Scopes

**BATCH scope:** File-level metadata affecting all rows.
- `IMPORT_TYPE` — Column headers ambiguous; infer from first row
- `SOURCE_CURRENCY` — Default EUR; ask only if amounts lack currency label
- `ACCOUNT_ASSIGNMENT` — **Never asked by default** if no account profiles exist; silent default `_unassigned`

**ENTITY scope:** One per distinct normalized company name.
- Map free-text company name to canonical `security_id`
- Possible answers: `SELECTED_CANDIDATE` (from DB), `CREATED_NEW_SECURITY`, `SKIPPED_COMPANY`
- Skipped rows are not committed; company name not added to alias map (user must re-answer in future import of same file)
- **Fan-out rule:** Answer applies to ALL rows with identical `empresa_normalized` value; no partial application

**ROW_GROUP scope:** Shared structural anomalies within resolved security.
- **Ambiguity fingerprint:** `{security_id}|{import_type}|{anomaly_type}|{key_params}`
- Examples: `RIGHTS_AMOUNT_CLASSIFICATION`, `ZERO_COST_CLASSIFICATION`, `PRICE_CURRENCY_CLARIFICATION`, `COMMISSION_IN_TOTAL_CLARIFICATION`
- **Fan-out rule:** Answer applies to ALL rows sharing exact fingerprint; user can "Handle rows individually" to break group

**ROW scope:** Per-row exceptions (rare, user-initiated).
- Appears when user opts to handle ambiguous group row-by-row
- Does not require answer for every row; unanswered rows inherit ROW_GROUP answer if any, or skip

### Inline Security Creation — Atomic Protocol

When import chat encounters a company with no canonical identity:

**Step 1 (user-initiated in chat):** User clicks "Create Security" → sub-form appears inline
**Step 2 (deterministic validation):** Service checks:
  - ISIN collision (no duplicate ISIN in `security_master`)
  - Ticker/MIC collision (multiple docs OK; full `security_id` checked)
  - Alias collision (mapped names don't already resolve to other security)
  - **Blocking:** If collision found, return user-facing message ("This ISIN already exists as..."); sub-form stays open, user corrects

**Step 3 (atomic write):**
  - Create `security_master` in `symbols` container, partition `{ticker}`
  - **Simultaneously** update `import_session.entity_group[{company_key}]`:
    - `.status = "AUTO_RESOLVED"`
    - `.resolved_security_id = new_security_id`
    - `.answer_type = "CREATED_NEW_SECURITY"`
  - **Atomicity:** Both writes must succeed together (Cosmos transactional batch within single partition). On failure, both roll back; user sees "Creation failed; please retry" and tries again.

**Step 4 (session state):** ENTITY question for this company is immediately marked answered; no follow-up required. Conversation resumes with next unanswered ENTITY question or transitions to ROW_GROUP questions.

**Why atomic:** The `import_session` document is the source of truth for which securities have been resolved. Partial writes create ambiguity (is the security created but session not updated? vice versa?). Cosmos transactional batch ensures consistency.

**Crash recovery:** If write partly succeeds (security created, session not updated) or crashes mid-operation:
- Service detects on next POST (retry): security already exists → check if session also updated. If not, update session atomically with the found security ID
- `creation_intent` field in session (optional, future) could track "I intended to create X" for stronger recovery, but atomic write on retry is sufficient for MVP

---

## Security Identity & Ticker-Only Legacy Routes

### MIC:TICKER Format Rationale

| Factor | MIC:TICKER (adopted) | TICKER:MIC (overruled) |
|--------|----------------------|------------------------|
| **Namespace convention** | ✅ Broader scope first (standard in URIs, DNS, TradingView) | Ticker first breaks convention |
| **Sort order** | ✅ Groups by exchange alphabetically (useful for UI) | Groups by ticker; exchange requires secondary sort |
| **URI paths** | `/securities/XNYS:AAPL` = "within NYSE, Apple" | `/securities/AAPL:XNYS` = "Apple on NYSE" (less hierarchical) |
| **Industry precedent** | TradingView, Bloomberg, financial APIs use exchange-first | Some broker UIs show ticker first (but not colon-separated) |
| **Documentation stability** | All prior design docs use MIC:TICKER | Retroactive change confuses existing context |

**Decision:** `MIC:TICKER` is canonical. All implementations must use this format.

### Legacy Ticker-Only Routes

Existing routes use bare tickers: `GET /api/symbols/AAPL`.

**Case 1 (common):** Ticker has exactly one `security_master`.
```
"AAPL" → partition "AAPL" has single sec_XNYS_AAPL → security_id = "XNYS:AAPL"
Unambiguous mapping; route works unchanged.
```

**Case 2 (cross-exchange collision):** Ticker has multiple `security_master` docs.
```
"SAN" → partition "SAN" has sec_XMAD_SAN (Santander) and sec_XPAR_SAN (Sanofi)
```

Resolution strategy:
- If `symbol_config` exists (`config_SAN`), its new `security_id` field identifies which one
- If no `symbol_config` (portfolio-only), route returns HTTP 300 Multiple Choices with list of candidates
- For Phase 1, collisions unlikely (US-only existing symbols; non-US added via import)

**Bridge field:** Every `security_master` carries `legacy_symbol` (bare ticker) matching `symbol_config` in same partition (if exists).

---

## Consolidated Invariants & Behavioral Rules

### Ledger Invariants (Unchanged from Phase 1 MVP)

| # | Invariant | Enforced by |
|---|-----------|-------------|
| I1 | `txn_type ∈ {BUY, SELL, DIVIDEND, ca_leg_type ∈ {SCRIP_CASH, SCRIP_SHARE}}` | API validation |
| I2 | `quantity > 0` for all types; direction in txn_type | API validation |
| I3 | Every money field: amount + currency; eur_amount/fx_rate when currency ≠ EUR | API validation |
| I4 | `withholding_destination: null` ≠ `{amount: 0}`; UI renders null as "Pending" | UI + API validation |
| I5 | Derived holdings never negative: `holdings = SUM(BUY) - SUM(SELL) + SUM(ca_leg_shares)` | Chronological ledger check |
| I6 | Movements immutable: corrections via soft-delete or new document, never in-place mutation | API design |
| I7 | `net = gross - fees - wht_source - wht_dest` (all in EUR) | Computed by API |
| I8 | Deleted rows (`deleted_at` set) excluded from aggregates | Query filters |
| I9 | No ledger write until user explicitly confirms in chat | Session state gate |
| I10 | Unresolved securities block commit; staged rows remain staged | ENTITY question required |

### Import Behavioral Rules

**No hidden writes:** Only two intentional writes during import:
1. Inline security creation (user clicks "Create" in chat, within ENTITY scope)
2. Ledger commit (user clicks "Confirm" at preview)

**Staged rows:** Parsed and stored temporarily in `portfolio._unassigned` with 90-day TTL. On commit, atomically moved (delete staged, create ledger). Idempotency key prevents double-writing if commit API retried.

**Currency handling:**
- All amounts dual-stored: transaction currency + EUR equivalent
- FX rate: `fx_rate = EUR_PER_TXN_CCY` (9 decimal places)
- Formula: `amount_eur = amount_txn × fx_rate` (always multiply)
- Default currency: EUR (no question unless file amounts are non-EUR without explicit label)
- Currency batch-required: All rows in a file must be same currency (enforced by import parser)

**Account handling:**
- Account optional; silent default `_unassigned` if not answered (no question asked unless profiles exist)
- All users can see `_unassigned` account; optional filtering by broker profile (later feature)
- Account is first-class ledger attribute (affects withholding, FX behavior per broker profile)

**Security resolution blocks commit:**
- Every row must have resolved `security_id` before ledger write
- Unresolved companies surface as ENTITY questions with candidates + create option
- If user skips company, rows for that company are not committed in batch; appear in next re-upload

---

## Known Limitations & Future Phases

- ❌ **Ticker-only security in portfolio:** Not supported; all ledger rows require full `security_id` (MIC:TICKER)
- ❌ **Portfolio-only securities without `symbol_config`:** Supported; create during import; no watchlist impact
- ❌ **Materialized ledger views:** Computed on read (acceptable for <500 movements); snapshot optimization deferred to Phase 3
- ❌ **User's pasted financial rows stored permanently:** Import stores only normalized metadata + deterministic schemas; raw pasted data discarded
- ❌ **Cost-basis methods beyond average:** Only average-cost MVP; FIFO/LIFO deferred to Phase 3
- ❌ **Fiscal export:** Deferred to Phase 4
- ❌ **Charts/analytics/time-series:** Deferred to Phase 5

---

## Consolidation Checklist

✅ Container inventory consolidated (symbols, portfolio, import_sessions)  
✅ Security master location resolved (symbols, not portfolio._global)  
✅ Security ID format confirmed (MIC:TICKER)  
✅ Import session container isolated (import_sessions, not portfolio._global)  
✅ Conversational chat architecture preserved (LLM orchestration + deterministic code)  
✅ Inline security creation protocol atomic (transactional batch within partition)  
✅ Ledger invariants restated (BUY/SELL/DIVIDEND model, cost basis, withholding)  
✅ Import behavioral rules centralized (no hidden writes, staged rows, currency batch, account optional)  
✅ Ticker collision handling documented (symbol_config bridge, 300 response fallback)  
✅ TTL isolation confirmed (portfolio doc-controlled, staged_import_row 90d, import_session 7d)  
✅ All prior design decisions preserved (FX convention, null-vs-zero withholding, ca_event/ca_leg)  

---

**See also:**
- Orchestration logs: `.squad/orchestration-log/` (agent histories & reconciliation)
- Original inbox designs: Inbox files cleared after this consolidation; all content merged here
