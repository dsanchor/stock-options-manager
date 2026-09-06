# Security Identity, Master Catalog, and Import Matching

**Author:** Livingston (Persistence & Integration Engineer)  
**Date:** 2026-09-05  
**Revised:** 2026-09-05T17:29Z — per `copilot-directive-20260905T172200+0200.md`: adds §5.4 (single catalog guarantee), replaces §10 with full transactional inline creation protocol, resolves open question Q1 (symbols container confirmed as the single catalog).  
**Status:** DESIGN ONLY — supersedes the `security_alias_map` design in the prior import contracts and the "link via ticker symbol (string), not foreign key" decision in `decisions.md Decision #1`. No production edits.  
**Directives:** `copilot-directive-20260905T171218+0200.md`, `copilot-directive-20260905T172200+0200.md`, `copilot-directive-20260905T154228+0200.md`  
**Depends on:** `livingston-purchase-csv-import.md`, `livingston-sales-csv-import.md`, `livingston-dividend-csv-import.md`

---

## 1. Scope and What Changes

This document supersedes three specific design choices made in earlier contracts:

| Earlier decision | Superseded by |
|-----------------|---------------|
| `WARNING_SECURITY_UNRESOLVED`: import with null ISIN, resolve later | **Blocking gate**: rows with unresolved companies are staged, not committed, until the company is mapped to a canonical security |
| `security_alias_map` document as a flat alias→ISIN registry | **Security master catalog**: a proper `security_master` document per listing with canonical `security_id`, aliases, provider symbols, and lifecycle state |
| "Link via ticker symbol (string), not foreign key" | **`security_id = ticker:MIC`** as the stable canonical identifier, embedded in every ledger document |
| §10 abstract "new security sub-flow" requiring a separate UI step | **Transactional inline creation protocol** (`copilot-directive-20260905T172200+0200.md`): creation happens within the import conversation; ticker+MIC and ISIN collision checks run first; idempotent multi-step writes across containers; crash-recovery via `creation_intent`; session reference is updated atomically within the `import_sessions` partition |

The rest of the import architecture (the `_unassigned` partition, account-optional policy, dedup framework, arithmetic checks, and `ca_event`/`ca_leg` model) is unchanged.

**New principle:** Security identity is required before any ledger row is committed. Account identity remains optional (no warning for unassigned account). This is the authoritative constraint for the full import pipeline.

---

## 2. Why Ticker Alone Is Insufficient

The current system partitions the `symbols` container by ticker (`/symbol = "AAPL"`). This works for US equities analyzed via yfinance (which uses US tickers natively) but fails for a multi-exchange dividend portfolio:

| Problem | Example |
|---------|---------|
| Same ticker, different exchanges | `ALM` on BME (Almirall) vs. `ALM` on other markets |
| Same company, multiple listings | Unilever: `ULVR` on XLON (GBP) and `UL` on XNYS (USD) |
| Currency ambiguity | Without MIC, a price in the record has no unambiguous currency |
| Merger/spinoff tracking | Ticker changes break historical identity |
| Non-US securities | BME, XETR, Euronext Amsterdam, SIX, LSE all have their own ticker namespaces |

A `security_id = "{ticker}:{MIC}"` solves the exchange-collision problem, preserves historical identity through ticker changes, and is constructible without an external ID authority (ISIN remains optional).

---

## 3. `security_id` — The Canonical Identifier

### 3.1 Format

```
security_id = "{EXCHANGE_TICKER}:{ISO_10383_MIC}"

Examples:
  AAPL:XNAS    → Apple Inc., NASDAQ
  ULVR:XLON    → Unilever PLC, London Stock Exchange
  IBE:XMAD     → Iberdrola S.A., Bolsa de Madrid (BME)
  ALV:XETR     → Allianz SE, XETRA (Frankfurt)
  ASML:XAMS    → ASML Holding, Euronext Amsterdam
  NESN:XSWX    → Nestlé S.A., SIX Swiss Exchange
  KO:XNYS      → The Coca-Cola Company, NYSE
```

**Properties:**
- Stable for the lifetime of the listing; changes only when the exchange ticker changes (not on company rename alone)
- Encodes both the ticker and the exchange — no ambiguity
- Human-readable and debuggable
- Constructible by the user without any external data source
- Does not require ISIN (which may be absent for historical or small-cap securities)
- Uppercase only; colon as separator; never contains spaces

**Known ISO 10383 MICs for the four initial broker profiles:**

| Exchange | MIC | Currency | Notes |
|----------|-----|---------|-------|
| NASDAQ | XNAS | USD | US growth/tech |
| NYSE | XNYS | USD | US blue chip |
| London Stock Exchange | XLON | GBP | UK stocks |
| Bolsa de Madrid (BME) | XMAD | EUR | Spanish stocks |
| XETRA (Frankfurt) | XETR | EUR | German stocks |
| Euronext Amsterdam | XAMS | EUR | Dutch stocks |
| SIX Swiss Exchange | XSWX | CHF | Swiss stocks |
| Euronext Paris | XPAR | EUR | French stocks |
| Euronext Brussels | XBRU | EUR | Belgian stocks |
| Euronext Lisbon | XLIS | EUR | Portuguese stocks |

The MIC list is extensible without schema changes (it is a string field).

### 3.2 Partition Key in the `symbols` Container

A `security_master` document lives in the `symbols` container, partitioned by the ticker portion of the `security_id`. This collocates it with the existing `symbol_config` for the same ticker, enabling single-partition reads.

```
symbols container, partition = "AAPL":
  id: "config_AAPL"          doc_type: symbol_config      (existing, unchanged)
  id: "sec_AAPL:XNAS"        doc_type: security_master    (new)
  id: "act_..."              doc_type: activity            (existing, unchanged)

symbols container, partition = "ULVR":   (new partition; no symbol_config)
  id: "sec_ULVR:XLON"        doc_type: security_master    (new, no symbol_config needed)
```

For securities that are portfolio-only (not on the watchlist), the partition exists solely for the `security_master` document. This is valid — a Cosmos partition does not require a `symbol_config` to exist.

---

## 4. `security_master` Document Schema

```jsonc
{
  // ── Identity ────────────────────────────────────────────────────────
  "id": "sec_AAPL:XNAS",
  "symbol": "AAPL",                    // partition key — ticker as traded on this exchange
  "doc_type": "security_master",

  "security_id": "AAPL:XNAS",         // canonical identifier; denormalized for queries
  "ticker": "AAPL",                   // exchange-local ticker
  "exchange_mic": "XNAS",             // ISO 10383 Market Identifier Code
  "exchange_name": "NASDAQ",          // human-readable exchange name (display only)
  "trading_currency": "USD",          // currency in which this security trades on this exchange

  // ── Names ────────────────────────────────────────────────────────────
  "display_name": "Apple Inc.",       // preferred UI name; may be shortened
  "legal_name": "Apple Inc.",         // full legal name; for fiscal export
  "short_name": "Apple",             // 1–2 word abbreviation for compact UI

  // ── Optional canonical identifiers ───────────────────────────────────
  "isin": "US0378331005",            // ISO 6166; optional but strongly recommended
  "cusip": "037833100",              // US CUSIP; optional
  "sedol": null,                     // UK SEDOL; optional

  // ── Provider-specific symbols ────────────────────────────────────────
  // Used by data-fetching components; not for identity. Keys are extensible.
  "provider_symbols": {
    "yfinance": "AAPL",             // as passed to yf.Ticker()
    "tradingview": "NASDAQ:AAPL",   // as used in TradingView URLs
    "ibkr_conid": null              // IBKR contract ID (if known)
  },

  // ── Import aliases ───────────────────────────────────────────────────
  // Company names that map to this security in import files.
  // Normalized comparison (NFKD lowercase, no legal suffixes).
  // Each entry has provenance.
  "aliases": [
    {
      "raw": "Apple Inc.",
      "normalized": "apple",
      "added_by": "system",
      "added_at": "2026-09-05T00:00:00Z",
      "source": "DISPLAY_NAME_AUTO"
    },
    {
      "raw": "APPLE INC",
      "normalized": "apple",
      "added_by": "dsanchor",
      "added_at": "2026-09-05T10:30:00Z",
      "source": "IMPORT_CONFIRMED",
      "confirmed_from_import_batch": "importbatch_..."
    }
  ],
  // aliases are append-only (voided, not deleted) for audit purposes

  // ── Asset classification ──────────────────────────────────────────────
  "asset_class": "EQUITY",           // EQUITY (only class in scope for Phase 1b)
  "country_of_incorporation": "US",  // ISO 3166-1 alpha-2
  "sector": null,                    // from enrichment; optional

  // ── Status and lifecycle ──────────────────────────────────────────────
  "status": "ACTIVE",
  // Enum: ACTIVE | DELISTED | MERGED | SPINOFF_SOURCE | TICKER_CHANGED
  "status_date": null,               // date when status changed from ACTIVE
  "delisted_date": null,

  // Corporate action linkage (immutable chain):
  "superseded_by": null,
  // security_id of the replacement (after ticker change, merger, or relisting)
  "supersedes": null,
  // security_id this record replaced (if created as a result of a corporate action)
  "corporate_action_note": null,
  // Free text; e.g., "Ticker changed from FB to META on 2022-06-09"

  // ── Cross-listings (same underlying company, different exchanges/currencies) ──
  "related_securities": [
    // { "security_id": "UL:XNYS", "relationship": "SAME_COMPANY_FOREIGN_LISTING" }
    // { "security_id": "BRK.B:XNYS", "relationship": "SHARE_CLASS_SIBLING" }
    // { "security_id": "XYZ_SPIN:XNAS", "relationship": "SPINOFF_CHILD" }
  ],
  // Relationships are informational; they do not change identity or holdings derivation

  // ── Watchlist/portfolio presence flags ───────────────────────────────
  "has_symbol_config": true,
  // true if a symbol_config document exists in this partition.
  // Maintained by the system on symbol_config create/delete.

  "portfolio_tracked": false,
  // true if any active ledger_txn or ca_leg references this security_id.
  // Maintained best-effort by a post-import trigger.

  "created_at": "...",
  "updated_at": "...",
  "created_by": "dsanchor"
}
```

---

## 5. Migration from `symbol_config` Identity to `security_master`

### 5.1 What Stays the Same

Every existing `symbol_config` document, `activity`, `alert`, `report`, and `enrichment_history` document remains **unchanged**. Every existing API route, agent runner path, options cache, and `best_options` precompute path continues to use the ticker string as the primary key (`cosmos.get_symbol("AAPL")`). No agent logic, no options route, no cache key changes.

This is a non-negotiable migration constraint: adding the security catalog must not require touching options-chain caches, agent runners, or the 40+ files that handle the existing `symbol_config` pattern.

### 5.2 Additive Migration Steps

1. **Add `security_id` field to `symbol_config`** (soft addition, backward-compatible):
   ```jsonc
   "security_id": "AAPL:XNAS"   // new field; all existing code ignores it
   ```
   Populated by a one-time migration job that constructs `"{symbol}:{exchange_mic}"` for every existing `symbol_config` where `exchange_mic` is a known MIC. For legacy documents with non-MIC exchange strings, leave `security_id = null` until manually corrected.

2. **Create `security_master` documents** for all existing `symbol_config` symbols. The `security_master` is written alongside the existing documents in the same partition. Existing code never reads `doc_type: security_master` — no interference.

3. **Seeded aliases:** For each new `security_master`, the `display_name` and `symbol` are automatically added as `aliases` with `source: DISPLAY_NAME_AUTO`.

4. **New portfolio code** (ledger importers, portfolio API) reads `security_master` documents by `security_id`. Existing options code reads `symbol_config` by ticker. The two paths are entirely separate until a deliberate Phase 3 unification.

### 5.3 Legacy Ticker Lookup → `security_id`

For components that know only a ticker string and need the `security_id`:
```
security_id = "{ticker}:{exchange_mic}"
```
where `exchange_mic` comes from the `symbol_config.exchange` field (already populated). This lookup is a deterministic field read, not a database query.

For the import alias map lookup (empresa_raw → security_id): the `security_master.aliases[]` array is the authoritative index. See §7.

### 5.4 Single Catalog Guarantee — No Portfolio-Specific Identity Artifacts

**Every security identity artifact lives exclusively in the `symbols` container.** This is a hard architectural invariant, not a preference.

| Container | What it stores (identity-related) |
|-----------|-----------------------------------|
| `symbols` | `security_master` documents: canonical `security_id`, ticker, MIC, trading currency, display/legal name, ISIN, provider symbols, aliases with provenance, corporate-action lifecycle links (`superseded_by`, `supersedes`, `related_securities`), status |
| `portfolio_ledger` | `security_id` string references; time-of-write embedded `security` snapshots in `ledger_txn`/`ca_event` (point-in-time historical record, not authoritative); `staged_import_row` with `security_id` reference and `empresa_raw`/`empresa_normalized` strings needed only for staging |
| `import_sessions` | `entity_group.resolved_security_id` (reference); `import_question.answer.resolved_security_id` (reference); `creation_intent` object (transient crash-recovery record, not identity data — see §10.3) |

The embedded `security` snapshot in `ledger_txn` is intentional and correct: it preserves the security's identity as it was at the time of the transaction. It is a historical record, not a secondary catalog. Querying for securities must never target `portfolio_ledger` or `import_sessions` — only the `symbols` container.

This invariant is enforced structurally: a dedicated `SecurityCatalogService` is the sole writer to `security_master` documents. No other service component, no import pipeline step, and no LLM call path has write access to the `symbols` container for `security_master` documents. All security creation, alias addition, and lifecycle mutation routes through `SecurityCatalogService`.

**Compatibility with existing routes:** All existing API routes and agent components that read from the `symbols` container continue to operate unchanged. They query `symbol_config` documents (by ticker partition key); `security_master` documents coexist in the same partitions and are invisible to queries that filter `doc_type = 'symbol_config'`. The `SecurityCatalogService` reads both doc types within the same partition when it needs to present the unified catalog view.

---

## 6. Unified Security Catalog — Portfolio Without Watchlist

The new directive overrides the earlier "watchlist and portfolio are entirely independent" decision. A security now appears in one unified catalog regardless of which subsystem uses it:

| `has_symbol_config` | `portfolio_tracked` | Displayed in |
|--------------------|--------------------|----|
| `true` | `false` | Symbols/Watchlist page (existing), Securities page (zero holding) |
| `false` | `true` | Securities page only (portfolio-only holding) |
| `true` | `true` | Both pages; linked via `security_id` |
| `false` | `false` | Catalog only; not in either active page (created but unused) |

**Securities page behavior for portfolio-only holdings (`has_symbol_config = false`):**
- Shows holding quantity, average cost, net invested
- Shows no watchlist flags, no agent activity, no options data
- "Add to Watchlist" button: creates a `symbol_config` document → `has_symbol_config` becomes `true` → security now appears on both pages
- No yfinance enrichment unless added to watchlist (enrichment is a watchlist concern, not a portfolio concern)

**Watchlist-only securities with zero holdings:** `portfolio_tracked = false` means the security appears on the Securities page with quantity = 0, avg_cost = "—", as a placeholder row. This makes the two pages composable without duplication.

---

## 7. Import Matching Pipeline

### 7.1 Pre-Import Preview Contract

**Before any rows are committed to `portfolio_ledger`, the import API returns a preview:**

```jsonc
{
  "batch_id": "importbatch_...",
  "status": "AWAITING_SECURITY_RESOLUTION",

  "rows_total": 85,
  "rows_matched": 62,          // company resolved; ready to commit
  "rows_staged": 18,           // company unresolved; pending mapping
  "rows_skipped_errors": 5,    // parse errors; not written at all

  "unresolved_companies": [
    {
      "empresa_raw": "Coca Cola Company",
      "empresa_normalized": "coca cola",
      "affected_row_count": 14,      // how many import rows are blocked
      "affected_years": [2018, 2019, 2020, 2021],
      "candidates": [                // from the matching pipeline; see §7.2
        {
          "security_id": "KO:XNYS",
          "display_name": "The Coca-Cola Company",
          "ticker": "KO",
          "exchange": "NYSE",
          "isin": "US1912161007",
          "match_stage": "STAGE_4_NORMALIZED_NAME",
          "confidence": "HIGH",
          "reasons": ["'coca cola' contained in canonical name 'the coca-cola company'"]
        },
        {
          "security_id": "COKE:XNAS",
          "display_name": "Coca-Cola Consolidated Inc.",
          "ticker": "COKE",
          "exchange": "NASDAQ",
          "match_stage": "STAGE_5_AI_ADVISORY",
          "confidence": "LOW",
          "reasons": ["name overlap; however 'Consolidated' is a distinct company"]
        }
      ]
    }
    // ... more unresolved companies
  ],

  "resolved_companies": [
    { "empresa_raw": "Apple Inc.", "security_id": "AAPL:XNAS", "match_stage": "STAGE_1_EXACT_ALIAS", "row_count": 8 }
    // ...
  ]
}
```

The user sees this preview before committing anything. Resolved-company rows are committed immediately on preview generation (they are unambiguous). Unresolved-company rows are held in staging documents (§9). No row is lost.

### 7.2 Five-Stage Matching Pipeline

Each `empresa_raw` value is run through stages sequentially. The first stage that produces a **definitive** match halts processing for that company. Advisory candidates (Stages 4 and 5) are returned for user confirmation.

#### Stage 1 — Exact Alias Match

```
lookup_key = normalize(empresa_raw)   // NFKD lowercase, strip legal suffixes, collapse spaces
```

Search `security_master.aliases[].normalized` across all active `security_master` documents (indexed cross-partition query on the `aliases.normalized` composite field).

If exactly one security_master matches: **definitive match**. No user confirmation required if `source = IMPORT_CONFIRMED` (a previously confirmed alias). If the alias was auto-seeded (`DISPLAY_NAME_AUTO`), treat as a candidate (user confirms once).

If multiple security_masters match the same normalized alias: ambiguous → Stage 4.

#### Stage 2 — Exact Provider Ticker + Market

Applies only when the batch supplies `source_market_mic` (user tells the importer "all securities in this file are from exchange X"). In that case:
```
candidate_security_id = "{empresa_raw_uppercase}:{source_market_mic}"
```
Look up `security_master` with this `security_id`. If found: **candidate** (not definitive — ticker-as-company-name is fragile). Present to user for confirmation.

This stage is skipped when `source_market_mic` is absent.

#### Stage 3 — Exact ISIN

If `empresa_raw` matches the ISIN format (`[A-Z]{2}[A-Z0-9]{9}[0-9]`, 12 chars): look up `security_master.isin` (cross-partition, indexed). If exactly one match: **candidate** (ISIN is strong but check the display name makes sense to the user).

This stage is primarily useful for future formats that may include ISIN columns. In the current 6–8 column CSV formats, ISINs do not appear.

#### Stage 4 — Normalized-Name Candidate

Run a scored full-text search against all `security_master` documents:
- Exact normalized match on `aliases[].normalized`: score 1.0
- Exact normalized match on `display_name` (normalized): score 0.95
- Substring containment: score 0.7 × coverage_ratio
- Token overlap: score 0.5 × overlap_ratio

Return up to 10 candidates, ordered by score. Present to user with match reasons. Stage 4 alone never produces a definitive match — always requires user confirmation.

#### Stage 5 — AI Advisory Suggestion

Invoked only when Stages 1–4 produce no high-confidence candidate (score < 0.7) or when the user explicitly clicks "Ask AI for suggestions."

**AI model receives:**
- The unresolved `empresa_raw` string
- Optional context: country hint (from batch metadata), sector if guessable, years of data
- The list of existing `security_master` entries (or a summary embedding of the catalog)

**AI model produces:**
- An ordered list of existing `security_id` values from the catalog (not invented ones)
- For each: `confidence` (HIGH | MEDIUM | LOW) and `reasons` (array of strings)

**AI constraints (hard rules, enforced at the API layer, not by prompting alone):**
- Output is validated against the existing catalog before being shown to the user; any `security_id` not in the catalog is silently dropped
- AI never writes to the alias map
- AI never writes `security_master` documents
- AI output is labeled "AI suggestion — requires your confirmation" in the UI
- A "No match in catalog" response is valid and expected

The AI call is made to the existing AI agent infrastructure (same pattern as other agents in the system — pre-fetched context, structured JSON output, no direct database access from the model).

### 7.3 Determinism Guarantee

The five stages are run in fixed order. The same `empresa_raw` + same catalog state always produces the same candidates. Stage 5 (AI) is the only non-deterministic stage, and its output is advisory — it does not affect which rows are committed or how the ledger is structured. The AI output affecting the ledger requires an explicit user confirmation.

---

## 8. User Confirmation and Durable Alias Mapping

### 8.1 Confirmation Creates an Immutable Alias Entry

When the user selects a candidate for an unresolved company:

1. A new entry is appended to `security_master.aliases[]`:
```jsonc
{
  "raw": "Coca Cola Company",
  "normalized": "coca cola",
  "added_by": "dsanchor",
  "added_at": "2026-09-05T11:45:00Z",
  "source": "IMPORT_CONFIRMED",
  "confirmed_from_import_batch": "importbatch_...",
  "match_stage_used": "STAGE_4_NORMALIZED_NAME",
  "ai_was_consulted": false,
  "void": false,
  "void_reason": null
}
```

2. Alias entries are **append-only within the array**. To revoke or correct an alias: set `void: true` on the existing entry and append a corrected entry. The correction has `replaces_raw` referencing the voided entry. This provides a full audit trail of alias decisions.

3. **Scope:** Once confirmed, this alias resolves for all future imports — not just the current batch. The user never needs to re-confirm the same company name.

4. **Cascade to staged rows:** All staged rows for the now-resolved `empresa_normalized` are immediately promoted to "ready to commit." The import pipeline processes them using the staged row documents (§9) — no file re-upload needed.

### 8.2 "Skip This Company" Option

The user may explicitly skip an unresolved company rather than mapping it:
- All staged rows for that company are voided (not committed)
- The skip is recorded in the import_batch document: `skipped_companies: [{ empresa_raw: "...", row_count: 14, skipped_by: "dsanchor", skipped_at: "..." }]`
- Skipped rows are counted in batch summary but not in the ledger
- A future import of the same file with the same company would re-surface it as unresolved (the skip is batch-specific, not a permanent alias decision)

### 8.3 Alias Scope and Conflicts

A given normalized alias can map to only one `security_id` at a time (active + non-voided). If a user tries to map "coca cola" to a second security, the system warns: "This name is already mapped to KO:XNYS. Do you want to remap it?"

Remapping voids the old alias entry and creates a new one. Historical ledger entries that were imported using the old mapping retain the old `security_id` (point-in-time identity) — the remap only affects future imports.

---

## 9. Staged Rows — Preserving Import Data Without File Re-Upload

### 9.1 `staged_import_row` Document

When a company is unresolved at preview time, the parsed (but not committed) row data is stored in `portfolio_ledger` as a staging document:

```jsonc
{
  "id": "staged_{batch_id}_{row_sha256_8chars}",
  "account_id": "_unassigned",     // or the specified account_id; matches future ledger partition
  "doc_type": "staged_import_row",

  "batch_id": "importbatch_...",
  "empresa_raw": "Coca Cola Company",
  "empresa_normalized": "coca cola",
  "csv_row_number": 47,
  "row_sha256": "...",             // for dedup on commit

  // Parsed monetary and date fields (the minimal set needed to create a ledger entry):
  "import_type": "DIVIDEND",       // DIVIDEND | PURCHASE | SALE
  "trade_date": "2021-03-15",
  "quantity": "50.0000000000",
  "gross_txn": "234.560000",
  "fees_txn": "0.000000",
  "net_txn": "176.850000",
  "origin_wht_txn": "35.180000",
  "dest_wht_txn": "22.520000",
  "txn_currency": "EUR",
  "csv_año": 2021,
  // ... other import-type-specific parsed fields

  // TTL: staged rows auto-expire after 90 days if not processed
  "ttl": 7776000,   // 90 × 24 × 3600 seconds (CosmosDB TTL in seconds)
  "staged_at": "2026-09-05T11:00:00Z",
  "staged_by": "dsanchor"
}
```

**What is NOT stored:** The raw row string (unless `store_raw_rows = true`). The financial data is already parsed — the staging document holds the parsed fields, not the original text.

**Why this is acceptable:** Staged rows are temporary (90-day TTL). They hold the same parsed financial amounts that would be in a committed `ledger_txn`. The sensitivity level is identical to what the ledger already stores. The TTL prevents indefinite accumulation.

### 9.2 Commit After Security Resolution

When a company is mapped to a `security_id`:
1. All `staged_import_row` documents for `{batch_id} × {empresa_normalized}` are fetched (single partition query, index on `empresa_normalized`).
2. For each staged row: create the appropriate `ledger_txn` or `ca_event`/`ca_leg` documents (using the now-resolved `security_id`).
3. The staged document is voided (`status: committed`; not deleted — maintains audit trail until TTL).
4. The `import_batch.summary` is updated: the committed rows move from `rows_staged` to `rows_imported_clean`.

### 9.3 Idempotency

If the user accidentally triggers "commit staged rows" twice:
- The `row_sha256` on each staged row is checked against the ledger before writing.
- Already-committed rows (`row_sha256` exists as active `ledger_txn`) produce `SKIPPED_DUPLICATE`.
- No double-counting; no error.

---

## 10. Inline Security Creation — Transactional Protocol

Security creation from within an import session is an **inline** operation: the user never leaves the import conversation. The creation form appears as a step inside the ENTITY question. When completed, the session's entity group is immediately resolved and the import can continue.

Because the `security_master` is written to the `symbols` container while the session state update is written to `import_sessions`, the two writes cannot be made atomic with a single Cosmos transaction. The protocol is instead designed as an **idempotent write sequence** with crash recovery.

### 10.1 Pre-Creation Collision Checks

Before presenting the creation form to the user, the service runs two checks synchronously. Both are reads-before-write and must complete before the form is shown.

**Check A — Primary collision (ticker + MIC):**

```
candidate_security_id = "{user_ticker_input}:{user_mic_input}"
```

Query: read `id = "sec_{candidate_security_id}"` from partition `= "{user_ticker_input}"` in the `symbols` container. This is a point read (O(1), single partition).

| Result | Action |
|--------|--------|
| Document exists, `status = ACTIVE` | Security already exists. Redirect: present it as a new candidate in the ENTITY question. The user maps to it rather than creating. |
| Document exists, `status = DELISTED` or `status = TICKER_CHANGED` | Collision with a legacy/delisted entry. Present to user: "This ticker+exchange is recorded as delisted/changed. Do you want to map to the successor, or create a new entry under this identifier?" User decides. |
| Document not found | Collision-free. Proceed to Check B. |

**Check B — ISIN collision (when ISIN is provided):**

If the user provides an ISIN, execute a cross-partition query on the `symbols` container:
```
SELECT c.security_id, c.display_name, c.exchange_mic FROM c
WHERE c.doc_type = 'security_master' AND c.isin = @isin
```

This query is cross-partition (ISIN is not the partition key). For a personal portfolio catalog with ~50–200 securities, this is acceptable. A secondary composite index on `(doc_type, isin)` must be provisioned to make this efficient.

| Result | Action |
|--------|--------|
| No match | ISIN is unique in the catalog. Proceed with creation. |
| One match, same `security_id` as user's intended ticker+MIC | Degenerate case: the document already exists (would have been caught by Check A). No-op. |
| One match, different `security_id` | Another listing of the same underlying security (cross-listing or different share class). **Warn:** "ISIN `{isin}` is already assigned to `{display_name}` ({security_id}). This may be a different listing of the same company. Create anyway, or map to the existing entry?" User decides. The warning is informational — creating a second entry with the same ISIN is allowed (valid for cross-listed securities). |
| Multiple matches | Catalog data issue; warn and require user to resolve before proceeding. |

### 10.2 Creation Form Fields

The form is presented inline within the chat step. The UI (Rusty's concern) renders it as a sub-step of the ENTITY question, not a navigation away from the import conversation.

| Field | Required | Default / Auto-fill |
|-------|----------|---------------------|
| Ticker | Yes | Pre-filled from `empresa_raw` if it looks like a ticker (all-caps, 1–5 chars); otherwise blank |
| Exchange MIC | Yes | Blank; searchable dropdown pre-populated with known MICs from §3.1 |
| Trading currency | Yes | Auto-filled from the selected MIC (e.g., XMAD → EUR); user may override |
| Display name | Yes | Pre-filled from `empresa_raw` |
| ISIN | No | Blank |
| Legal name | No | Defaults to display name on submission |
| Short name | No | Defaults to first non-stop-word token of display name |

### 10.3 Idempotent Write Sequence

After collision checks pass and the user submits the form, the service executes the following steps in strict order. Each step is idempotent: if the service crashes and restarts after any step, it can resume from the last completed step by checking the state at each point.

A `creation_intent` object is written to the import_session entity group **before Step 1** as the crash-recovery anchor:

```jsonc
// Written to entity_group inside the import_session document (conditional write, _etag):
"pending_creation": {
  "status": "IN_PROGRESS",
  "idempotency_key": "creat_{session_id}_{empresa_normalized}",
  "intended_security_id": "IBE:XMAD",
  "intended_ticker": "IBE",
  "intended_mic": "XMAD",
  "intended_display_name": "Iberdrola S.A.",
  "intended_isin": "ES0144580Y14",
  "intended_trading_currency": "EUR",
  "empresa_normalized_source": "iberdrola",
  "started_at": "2026-09-05T11:08:00Z",
  "last_step_completed": 0   // updated after each step
}
```

If the service finds a `pending_creation` with `status = IN_PROGRESS` on session load, it resumes from `last_step_completed + 1`.

---

**Step 1 — Write `security_master` to `symbols` container**

```
id = "sec_{security_id}"
partition_key = "{ticker}"    // the ticker portion of security_id
```

Idempotency: before writing, point-read the document. If it already exists with `security_id = intended_security_id` → Step 1 already completed; advance `last_step_completed` to 1 and continue. If it exists with a different `security_id` → collision (caught by Check A earlier; this should not happen). If it does not exist → create.

The `security_master` document is written with `has_symbol_config: false`, `portfolio_tracked: false`, `status: ACTIVE`. No `symbol_config` is created.

Update `creation_intent.last_step_completed = 1` on the session document (conditional write).

---

**Step 2 — Write first alias entry to `security_master`**

Append the `empresa_normalized` as the first alias entry with `source: IMPORT_CONFIRMED` and provenance linking to the session and question:

```jsonc
{
  "raw": "Iberdrola",
  "normalized": "iberdrola",
  "added_by": "dsanchor",
  "added_at": "2026-09-05T11:08:05Z",
  "source": "IMPORT_CONFIRMED",
  "confirmed_from_import_session": "impsess_abc",
  "confirmed_from_question_id": "q_impsess_abc_0004",
  "void": false
}
```

Idempotency: check `security_master.aliases[]` for an entry with `normalized = empresa_normalized AND void = false` before appending. If found → Step 2 already completed.

This write uses a Cosmos patch operation (array append) rather than a full document replace, to minimize conflict with other concurrent alias additions. The patch must include `if-match: {security_master._etag}` for optimistic concurrency.

Update `creation_intent.last_step_completed = 2`.

---

**Step 3 — Update the import_session entity group**

Set `entity_group.resolved_security_id = intended_security_id` and `entity_group.resolution_status = RESOLVED` in the session document.

Also set `creation_intent.status = COMPLETED` and clear `creation_intent.last_step_completed` (or leave it at 2 for audit purposes).

This write is a conditional write on the session document (`if-match: {session._etag}`). If it fails due to concurrent modification: re-read, verify `resolved_security_id` is not already set (if it is, Step 3 already completed by another writer → success), then retry.

---

**Step 4 — Mark the import_question as answered**

Write the question answer:

```jsonc
"answer": {
  "answered_at": "2026-09-05T11:08:10Z",
  "answered_by": "dsanchor",
  "answer_type": "CREATED_NEW_SECURITY",
  "resolved_security_id": "IBE:XMAD",
  "new_security_created": true,
  "new_security_id": "IBE:XMAD",
  "alias_written": true,
  "alias_idempotency_key": "creat_impsess_abc_iberdrola"
}
```

Idempotency: if question already has `status = ANSWERED` with `resolved_security_id = intended_security_id` → no-op; Step 4 already completed.

---

### 10.4 Crash Recovery

If the service crashes after any step, it resumes by:

1. Reading the session document and finding `entity_group.pending_creation.status = IN_PROGRESS`.
2. Reading `last_step_completed` and jumping to the next step.
3. Each step's idempotency check ensures re-execution is safe.
4. Once `last_step_completed = 4` (or Step 4 completes), set `pending_creation.status = COMPLETED`.

**Time-bound crash recovery:** If `pending_creation` has `status = IN_PROGRESS` and `started_at` is more than 5 minutes ago, the session service treats it as a stalled creation and presents the user with an option to retry or cancel the creation. It does not auto-retry indefinitely to avoid creating duplicate security documents if Check A somehow failed.

### 10.5 Concurrent Creation Race Condition

Two browser tabs of the same user may simultaneously reach the same ENTITY question and both attempt to create a security with the same ticker+MIC:

- Tab A: runs Check A (no collision found) → Step 1: creates `security_master` → Step 2: writes alias → Step 3: updates session → Step 4: answers question
- Tab B (slightly behind): runs Check A → **collision detected** (Tab A's `security_master` now exists) → service does not present the creation form; instead, presents the newly created security as a candidate → Tab B user selects it → the question is answered with `answer_type: SELECTED_CANDIDATE` pointing to Tab A's new security

The session converges to the same `resolved_security_id` regardless of which tab "won." There is no risk of duplicate `security_master` documents.

### 10.6 Auto-Addition to Watchlist

Creating a security through the import flow **does not** create a `symbol_config` document. The `security_master` is created; `has_symbol_config: false`. The security appears on the Securities (portfolio) page but not on the Symbols/Watchlist page. The user can promote it to the watchlist from the Securities page at any time, which creates a `symbol_config` in the same partition — the `security_master.has_symbol_config` field is then updated to `true`.

This is the only path by which a portfolio-origin security enters the watchlist. It is always a deliberate user action.

---

## 11. Corporate Actions, Lifecycle Events, and Edge Cases

### 11.1 Ticker Change

1. Create a new `security_master` with the new `security_id` (new ticker, same MIC).
2. On the old `security_master`: set `status: TICKER_CHANGED`, `superseded_by: "{new_security_id}"`, `status_date: "{change_date}"`, `corporate_action_note: "Ticker changed from FB to META on 2022-06-09"`.
3. On the new `security_master`: set `supersedes: "{old_security_id}"`.
4. Existing ledger records retain the old `security_id` (historical identity). New imports after the change use the new `security_id`.
5. The alias map: add the old company names as aliases on the new `security_master` too (with `source: CORPORATE_ACTION_MIGRATION`). This ensures historical CSV imports using the old name still resolve to the new security after the change.

### 11.2 Company Rename (No Ticker Change)

Update `display_name` and `legal_name` on the existing `security_master`. Add the old name as an alias. `security_id` does not change. No ledger records need updating.

### 11.3 Merger

- Acquired company: `status: MERGED`, `superseded_by: {surviving_security_id}`.
- Surviving company: `related_securities` entry noting the merger.
- Ledger records for the acquired company retain the old `security_id`.
- Holdings calculation: the acquired position appears as a historical position under the old `security_id`. When the user receives the surviving company's shares (a corporate action), those are a new set of ledger records under the surviving `security_id`.

### 11.4 Spinoff

- Parent retains `security_id`. Add `related_securities: [{ security_id: "{spinoff_security_id}", relationship: "SPINOFF_PARENT" }]`.
- Spinoff: new `security_master` with `status: ACTIVE`, `supersedes: null`, `related_securities: [{ security_id: "{parent_security_id}", relationship: "SPINOFF_CHILD" }]`.
- The spinoff shares received are a `SHARE_ACQUISITION` ca_leg under the new security_id.

### 11.5 Share Classes

Same underlying company but different share classes (e.g., Berkshire A and B, or Alphabet A, B, C) are **separate** `security_master` documents with separate `security_id` values. They are linked via `related_securities` with `relationship: SHARE_CLASS_SIBLING`. Holdings are tracked independently (different prices, different quantities). The user manages them as separate securities.

### 11.6 Duplicate Company Names

Two genuinely different companies with the same display name (rare but real): each gets its own `security_master`. The alias resolution at Stage 4 returns both as candidates. The user selects the correct one. The alias is then confirmed under the correct `security_id`. A `normalized_disambiguation_note` on each `security_master` can help the user distinguish them (e.g., "Incorporated in Spain, listed on BME" vs. "Incorporated in Mexico, listed on BME").

### 11.7 Delisted Holdings

`status: DELISTED`, `delisted_date: "{date}"`. The `security_master` is retained permanently (immutable historical record). Holdings for this security continue to appear on the Securities page with a "Delisted" badge. The user can still record a SELL (if they sold at delisting or received a liquidation payment) which closes out the position.

---

## 12. Dedup and Batch Preservation with Staged Rows

### 12.1 Exact Batch Re-Submission

If the user submits the same file a second time (without any changes):
- Stage 1: Already-committed rows → `SKIPPED_DUPLICATE` (row_sha256 matches active ledger records)
- Already-staged rows → `SKIPPED_DUPLICATE` (row_sha256 matches active staged_import_row records)
- The second submission produces no new documents; returns the same preview state

### 12.2 File Re-Submission After Partial Resolution

If the user submits the file again after resolving some companies (but not all):
- Previously committed rows → `SKIPPED_DUPLICATE`
- Newly resolved companies' staged rows → committed immediately (they're now resolvable at Stage 1)
- Still-unresolved companies → new staged documents created (or existing ones refreshed with a new TTL)

The net effect: each re-submission of the same file makes incremental progress. The user can resolve companies one at a time and re-submit to commit those rows, without re-processing already-committed data.

### 12.3 Staged Row TTL Extension

If a staged row approaches its 90-day TTL without being committed (user has not resolved the company), the portal displays a "Staged import expiring in X days" notice on the import history page. The user can extend the TTL by clicking "Keep staged rows" (updates the TTL on all pending staged rows for that batch). This is an explicit administrative action — staged rows do not auto-extend.

---

## 13. `security_id` in Ledger Documents — Embedding Rule

Every `ledger_txn` and `ca_event`/`ca_leg` document embeds the full `security` object at write time. This object is **expanded** with `security_id` as the first field:

```jsonc
"security": {
  "security_id": "KO:XNYS",          // primary identity — stable canonical ID
  "isin": "US1912161007",             // from security_master at import time (may be null)
  "ticker": "KO",
  "exchange_mic": "XNYS",
  "trading_currency": "USD",
  "display_name": "The Coca-Cola Company",
  "legal_name": "The Coca-Cola Company",
  "asset_class": "EQUITY",
  // ... other fields as before
}
```

**No foreign-key lookup on read.** The embedded `security` object contains the identity as it was at import time. If the company renames later, historical records still show the historical name — correct behavior.

**For holdings derivation:** group by `(account_id, security.security_id)`. This is a change from the earlier "group by (account_id, isin)" — using `security_id` is more precise because ISIN is optional, and cross-listings with the same ISIN but different `security_id` values are tracked independently.

---

## 14. API Additions

New endpoints for the security master and import matching:

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/portfolio/securities/catalog` | List all security_master entries; filters: status, exchange, has_symbol_config, portfolio_tracked |
| `POST` | `/api/portfolio/securities/catalog` | Create a new security_master |
| `GET` | `/api/portfolio/securities/catalog/{security_id}` | Fetch a single security_master |
| `PATCH` | `/api/portfolio/securities/catalog/{security_id}` | Update display_name, legal_name, provider_symbols, status (restricted fields) |
| `POST` | `/api/portfolio/securities/catalog/{security_id}/aliases` | Add a new alias (provenance required) |
| `DELETE` | `/api/portfolio/securities/catalog/{security_id}/aliases/{alias_normalized}` | Void an alias (soft delete with reason) |
| `POST` | `/api/portfolio/import/preview` | Run the 5-stage matching pipeline on an uploaded file; returns preview with unresolved companies and candidates |
| `POST` | `/api/portfolio/import/resolve` | Confirm a security mapping for an unresolved company; commits staged rows |
| `POST` | `/api/portfolio/import/skip` | Skip an unresolved company for the current batch |
| `GET` | `/api/portfolio/import/batches/{batch_id}/staged` | List staged rows for a batch |
| `POST` | `/api/portfolio/import/match-suggestions` | Run Stages 4–5 on demand for a given empresa_raw string (used by UI to refresh suggestions) |

The AI suggestion (Stage 5) is invoked as an internal agent call within `/api/portfolio/import/match-suggestions` — the same agent pattern as existing AI workflows in the system.

---

## 15. Phase Placement

### 15.1 Revised Phase 1b Prerequisites

The security identity design changes the Phase 1b prerequisite list. Before any CSV import can run:

| Prerequisite | Status |
|-------------|--------|
| `portfolio_ledger` container provisioned | Required (unchanged) |
| `security_master` documents created for all securities the user intends to import | **New — now required before first import** |
| Alias map seeded (initial aliases from display names) | **New — auto-seeded on security_master creation** |
| `account_profile` documents | Optional (unchanged) |

**Practical implication for this migration:** The user must create `security_master` entries for their portfolio companies before the import can commit any rows. For companies already in the watchlist (existing `symbol_config`), this migration runs automatically (§5.2, Step 2). For new portfolio-only companies, the user creates them via the import preview flow or the security catalog UI.

The import preview (§7.1) is the user-friendly path: upload the file → see which companies are unresolved → create or map them → commit the rows. This flows naturally without requiring the user to pre-configure the entire security catalog before importing.

### 15.2 Implementation Order Within Phase 1b

1. Provision security catalog schema (`security_master` documents for existing watchlist symbols)
2. Build security catalog UI (create, search, edit)
3. Build import preview API (5-stage pipeline, staged rows)
4. Build resolution UI (company mapping, new security creation)
5. Build commit pathway (staged rows → ledger entries)
6. Build dividend / purchase / sale import forms (reuse the preview+resolve+commit pipeline)

Steps 1–3 must precede steps 4–6. Steps 4 and 5 (the three importer types) can be built in parallel once the pipeline (step 3) is ready.

---

## 16. Open Questions

| # | Question | Impact |
|---|----------|--------|
| **Q1** | ~~Should `security_master` live in the `symbols` container or in a new dedicated `securities` container?~~ **RESOLVED** (`copilot-directive-20260905T172200+0200.md`): `security_master` lives in the `symbols` container. A dedicated container would give cleaner cross-partition ISIN queries but would split the catalog across two containers — violating the single-catalog guarantee and complicating the watchlist/options compatibility requirement. The `symbols` container is the single catalog. A secondary composite index on `(doc_type, isin)` handles the ISIN collision check efficiently (see §10.1). | Resolved. |
| Q2 | For the AI suggestion (Stage 5), which model should be used — the same agent-framework model as options agents, or a lighter/faster model given this is a synchronous UX interaction? | Latency vs. quality; the current agent infrastructure may be too slow for a responsive import preview |
| Q3 | Should the normalized alias lookup (Stage 1/4) use a full-text index in Cosmos or an in-memory in-process index loaded from the catalog? For a personal portfolio of ~100–200 securities, an in-memory index (refreshed on security_master change) is faster and avoids cross-partition queries. | Performance and complexity trade-off |
| Q4 | The `ttl` field on `staged_import_row` documents uses CosmosDB's document-level TTL. This requires the `portfolio_ledger` container to have TTL enabled (with a default TTL of -1 = no expiry, letting documents control their own TTL). This is a provisioning decision. | Requires `scripts/provision_cosmosdb.sh` change |

---

## 17. Summary

**`security_id = "{TICKER}:{MIC}"`** is the canonical identifier for all securities in the system. It is exchange-specific (no ticker collisions), human-readable, constructible without external data, and stable until an explicit corporate action (ticker change, merger) changes the listing. ISIN is optional and stored as a secondary identifier.

**Single catalog in `symbols` container** (`doc_type: security_master`). This is the sole location for all security identity artifacts: aliases, provider symbols, lifecycle state. The `portfolio_ledger` and `import_sessions` containers store only `security_id` references and time-of-write embedded snapshots. No portfolio-specific duplicate catalog exists. `SecurityCatalogService` is the exclusive writer to `security_master` documents.

**Inline creation from import sessions** follows a four-step idempotent write sequence: (1) write `security_master` to `symbols`, (2) write first alias to `security_master`, (3) update entity group in `import_session`, (4) mark `import_question` answered. A `creation_intent` crash-recovery record is written before Step 1 so the service can resume from any failure point. Two-check collision detection (ticker+MIC point read, ISIN cross-partition query) runs before the form is shown. Race conditions between browser tabs resolve safely: the second tab finds the collision and maps to the already-created security. No duplicate `security_master` documents can result.

**Migration is additive:** new documents written alongside unchanged existing documents. All existing agent paths, options caches, and watchlist routes remain unaffected. Existing `list_symbols()` and `get_symbol()` patterns that filter `doc_type = 'symbol_config'` are invisible to `security_master` documents.

**Unified catalog:** A security can exist without a `symbol_config` (portfolio-only) or without ledger entries (watchlist-only). The Securities page shows all `security_master` entries with `portfolio_tracked = true`; the Symbols/Watchlist page shows those with `has_symbol_config = true`. Both views share the same `symbols` container data source.

**Security is a hard gate for import.** Rows with unresolved companies are staged (not committed) until the company is mapped to a `security_id`. Staged rows live in `staged_import_row` documents with a 90-day TTL — no file re-upload needed when the user later resolves the company. Account assignment remains optional with no warning.

**Five-stage matching pipeline:** Exact alias → provider ticker+MIC (if exchange context given) → ISIN → normalized name candidates → AI advisory suggestions. AI never writes mappings; every alias is confirmed by the user and stored with provenance. Confirmed aliases are durable and apply to all future imports.

**Corporate action support:** Ticker changes create a new `security_master` with `supersedes`/`superseded_by` links. Historical ledger records retain their original `security_id` (point-in-time identity). Company renames update fields without changing `security_id`. Mergers, spinoffs, and share classes are all modeled with `related_securities` links and status fields, with no retroactive mutation of historical records.
