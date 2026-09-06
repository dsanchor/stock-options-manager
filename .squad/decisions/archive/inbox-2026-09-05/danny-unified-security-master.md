# Unified Security Master — Architecture Recommendation

**Author:** Danny (Lead/Architecture)  
**Date:** 2026-09-05  
**Status:** PROPOSED — authoritative superseding recommendation  
**Supersedes:**
- Decision #1 §"Architecture — Domain Boundaries" (two-silo design)
- Decision #1 §"Security Identity Model" (ISIN-primary embedded-only design)
- Import consolidated RC-6 `ERROR_UNRESOLVED_SECURITY` definition (now refined)
- Prior statement "Do NOT rename Symbols or Watchlist"

**Directive:** `copilot-directive-20260905T171218+0200.md` — unified canonical security catalogue with orthogonal states, AI-assisted alias resolution, unresolved = not ingested, multi-exchange identity.

---

## 1. Problem Statement

The current system has a structural gap that the portfolio feature exposes:

**Current state:** `symbols` container, partition key `/symbol` (bare ticker string). Each `symbol_config` is identified by ticker alone (`"id": "config_AAPL"`). This works for US-only options tracking but fails for:

1. **Ticker collision:** `BHP` trades on ASX (AUD) and LSE (GBP). `SAN` is Santander (XMAD, EUR) and Sanofi (XPAR, EUR). Ticker-only identity cannot distinguish them.
2. **Portfolio needs canonical identity:** Dividends, BUY/SELL movements, and cost-basis calculations require unambiguous security identity across brokers (Fidelity uses CUSIP, IBKR uses conid, the CSV uses free-text company names).
3. **Import gating:** The user's directive requires that BUY/SELL/DIVIDEND rows are importable ONLY when linked to a canonical security already in the system. This implies a security master that exists independently of movements.
4. **Orthogonal states:** A security can be tracked-for-options, owned, both, or neither. The current conflation of "in watchlist" with "exists in system" prevents clean portfolio representation.

**Required outcome:** A single canonical security master shared by options tracking and portfolio ownership, extensible to any exchange/market.

---

## 2. Security Identity Design

### 2.1 The `security_id` — Stable Canonical Identifier

```
security_id = "{exchange_mic}:{ticker}"
```

**Examples:**
| security_id | Company | Exchange | MIC |
|-------------|---------|----------|-----|
| `XNYS:AAPL` | Apple Inc. | NYSE | XNYS |
| `XNAS:MSFT` | Microsoft Corp. | NASDAQ | XNAS |
| `XMAD:SAN` | Banco Santander | Bolsa de Madrid | XMAD |
| `XAMS:SHELL` | Shell plc | Euronext Amsterdam | XAMS |
| `XLON:GSK` | GSK plc | London Stock Exchange | XLON |
| `XSWX:NESN` | Nestlé S.A. | SIX Swiss Exchange | XSWX |

**Properties:**
- **Deterministic:** Same security always produces the same ID.
- **Human-readable:** Developer/user can understand at a glance.
- **Collision-free across exchanges:** SAN on XMAD ≠ SAN on XPAR.
- **Stable:** Does not change when company display name changes.

**MIC source:** ISO 10383 Market Identifier Codes. Use operating MIC (XNYS, not segment MIC ARCX) for consistency. Common MICs for user's markets:

| Market | Operating MIC |
|--------|--------------|
| NYSE | XNYS |
| NASDAQ | XNAS |
| Bolsa de Madrid (IBEX) | XMAD |
| Euronext Amsterdam | XAMS |
| London Stock Exchange | XLON |
| SIX Swiss Exchange | XSWX |

**Extensible:** Adding a new market (e.g., Frankfurt XFRA, Tokyo XTKS) requires no schema change — just a new MIC value.

### 2.2 Optional ISIN

ISIN is stored when known but NOT required. Rationale:

- Existing system is ticker-based. Requiring ISIN immediately would block migration.
- ISIN is not always readily available for historical imports (user may not know ISIN for all 2016-era purchases).
- For tax export (Phase 4), ISIN becomes important. By then, enrichment or user entry will have populated it.
- Ticker collisions are already resolved by MIC — ISIN is redundant for identity but valuable for cross-referencing.

```
isin: "US0378331005" | null    // optional, validated format when present
```

### 2.3 Full Security Document Schema

```json
{
  "id": "sec_XNYS_AAPL",
  "doc_type": "security",
  "account_id": "_global",
  "security_id": "XNYS:AAPL",
  "exchange_mic": "XNYS",
  "ticker": "AAPL",
  "display_name": "Apple Inc.",
  "isin": "US0378331005",
  "listing_currency": "USD",
  "country_of_domicile": "US",
  "asset_class": "equity",
  "broker_ids": {
    "fidelity_cusip": "037833100",
    "ibkr_conid": "265598"
  },
  "aliases": ["APPLE INC", "APPLE INC.", "Apple Computer"],
  "states": {
    "agent_tracking": {
      "covered_call": true,
      "cash_secured_put": false,
      "buy_tracker": true
    },
    "archived": false
  },
  "legacy_symbol": "AAPL",
  "created_at": "...",
  "updated_at": "..."
}
```

**Storage:** `portfolio` container, partition `_global`. All security documents are shared configuration, not per-account.

**`legacy_symbol`:** The ticker string that maps this security to the existing `symbols` container. This is the bridge field for migration (§7).

**`states.agent_tracking`:** Replaces the current `watchlist` flags on `symbol_config`. These flags control which options agents run on this security. They live on the canonical security document — the single source of truth for "what is this security and what do we do with it."

**`aliases`:** Normalized company names that resolve to this security during import. Replaces the separate `security_alias_map` document — aliases are now co-located with the security they resolve to. Normalization: trim → NFKD fold → uppercase → strip legal suffixes (S.A., PLC, Inc., Corp., Ltd., N.V., SE) → collapse spaces → & → AND.

**Ownership:** NOT stored on the security document. Holdings are always derived from the portfolio ledger: `SUM(BUY) - SUM(SELL) + SUM(SHARE_ACQUISITION)`. A security with no movements shows zero/no holdings — this is a valid state, not an error.

### 2.4 Why Not UUID?

UUIDs are opaque — no human can tell what `a3f7b2c1-...` refers to. For a personal application with <500 securities, human-readable IDs provide:
- Instant recognition in logs, queries, and URLs
- Easy manual correction if a security is miscategorized
- Natural sort by exchange then ticker
- Simpler debugging

If the user ever lists on 5,000+ exchanges with systematic collisions, a surrogate key layer can be added. Not needed now.

---

## 3. Unified Master — Options + Portfolio

### 3.1 Prior Architecture (Two Silos — Superseded)

```
symbols container (pk /symbol)         portfolio container (pk /account_id)
├─ symbol_config (watchlist,           ├─ movement (BUY/SELL)
│   positions[], total_shares)         ├─ ca_event (DIVIDEND)
├─ activity                            ├─ ca_leg
├─ alert                               ├─ account
├─ report                              └─ import_batch
└─ ...
```

**Problems:**
- Security identity defined twice (symbol_config AND embedded in movements)
- No canonical master — "Does AAPL exist in the system?" requires checking both containers
- Import can't verify security existence without cross-container query
- Agent tracking flags on one copy, ownership on the other

### 3.2 New Architecture (Unified Master)

```
portfolio container (pk /account_id)
├─ _global partition:
│   ├─ security (canonical master — shared by all features)
│   └─ import_batch
├─ {account_id} partitions:
│   ├─ account
│   ├─ movement (BUY/SELL)
│   ├─ ca_event (DIVIDEND)
│   └─ ca_leg
├─ _unassigned partition:
│   └─ (imported movements without account)

symbols container (pk /symbol)  ← UNCHANGED, continues operating
├─ symbol_config (options positions, agent results)
├─ activity, alert, report, etc.
```

**The canonical security master lives in `portfolio._global`.** Both the portfolio system and the options system reference it.

**The `symbols` container is NOT modified or migrated immediately.** It continues to operate exactly as today. The bridge is `legacy_symbol` on the security document (§7).

### 3.3 Orthogonal States

A security's relationship to different features is expressed through independent, non-exclusive attributes:

| State | Source | Example |
|-------|--------|---------|
| **Exists** | Has a `security` document in `_global` | AAPL is a known security |
| **Agent-tracked** | `states.agent_tracking.{covered_call, cash_secured_put, buy_tracker}` | AAPL is tracked for covered calls |
| **Owned** | Derived: `holdings(security_id) > 0` | User owns 150 shares of AAPL |
| **Has options positions** | `symbol_config.positions[]` in `symbols` container (via `legacy_symbol` bridge) | AAPL has 2 open covered calls |
| **Archived** | `states.archived = true` | User sold all AAPL and no longer tracks it |

**Zero holdings is a valid state.** A security can:
- Be tracked for agents with zero shares (pure options play, no underlying)
- Have shares but no agent tracking (long-term hold, no options writing)
- Have neither (archived, or just created for alias resolution before import)
- Have both (the common case: owned + actively writing options)

---

## 4. Import Gating — Security Resolution

### 4.1 The Rule

> **Every BUY, SELL, and DIVIDEND row is importable ONLY when linked to a canonical security already present in the system.**

Unresolved companies are NOT ingested. They are returned as a grouped list for the user to create or map first, then retry.

### 4.2 Resolution Pipeline

For each unique `Empresa` (company name) in the import file:

```
Step 1: Exact alias match (deterministic)
  → Normalize empresa_raw
  → Search security.aliases[] across all securities in _global
  → Exact match found? → AUTO-RESOLVE (no user confirmation needed)

Step 2: Identifier match (deterministic)
  → If empresa_raw looks like a ticker (all-caps, 1-5 chars), check for
    security where ticker = empresa_raw (within batch source_currency/exchange scope)
  → Exact ticker match with unambiguous exchange? → AUTO-RESOLVE

Step 3: AI-assisted fuzzy suggestion (requires confirmation)
  → Send unresolved empresa_raw values to AI agent with context:
    - List of all known securities (security_id, display_name, ticker, aliases)
    - The import batch's source currency and broker hint
  → AI returns ranked suggestions with confidence score
  → Suggestions displayed to user; user must CLICK to confirm
  → AI MUST NOT auto-confirm. No suggestion is acted upon without user click.

Step 4: Unresolved
  → Company name has no match and no accepted suggestion
  → User must CREATE a new security or MAP manually
  → Row remains unimportable until resolved
```

**Deterministic auto-resolve (Steps 1-2):** No user interaction needed. The UI shows ✅ with the resolved security. User can override if the auto-resolution is wrong.

**AI suggestion (Step 3):** Shows 🤖 with suggested match and confidence. User clicks "Accept" or "Reject". Multiple suggestions may be shown ranked. The AI sees only security metadata (ticker, name, aliases) — never the user's financial amounts.

**Unresolved (Step 4):** Shows ❌ with "Create security" or "Map manually" actions. Row count shows how many rows are blocked by this unresolved company.

### 4.3 Wizard Flow Change

The import wizard gains a dedicated **Security Resolution** step between Batch Metadata and Preview:

```
Step 0: Upload/Paste
Step 1: Batch Metadata (currency, account — unchanged)
Step 2: Security Resolution (NEW)
Step 3: Preview (only fully-resolved rows)
Step 4: Review & Import
```

**Step 2 — Security Resolution:**

```
┌──────────────────────────────────────────────────────────────┐
│  Step 2 of 4 — Resolve Companies                             │
│                                                              │
│  {N} unique companies found in {M} rows.                     │
│  {A} auto-resolved · {B} need confirmation · {C} unresolved │
│                                                              │
│  ── Auto-resolved (deterministic) ────────────────────────── │
│  ✅  "APPLE INC"  (47 rows) → XNYS:AAPL  [saved alias]     │
│  ✅  "MSFT"       (12 rows) → XNAS:MSFT   [ticker match]    │
│  ✅  "SANTANDER"  (31 rows) → XMAD:SAN    [saved alias]     │
│                                                              │
│  ── AI suggestions (confirm or reject) ──────────────────── │
│  🤖 "COCA COLA COMPANY" (8 rows)                             │
│     → XNYS:KO  "The Coca-Cola Co."  (confidence: 95%)       │
│     [ ✓ Accept ]  [ ✗ Reject ]  [ Map manually ]            │
│                                                              │
│  🤖 "REPSOL OIL & GAS" (4 rows)                              │
│     → XMAD:REP "Repsol S.A."  (confidence: 88%)             │
│     [ ✓ Accept ]  [ ✗ Reject ]  [ Map manually ]            │
│                                                              │
│  ── Unresolved (create or map) ──────────────────────────── │
│  ❌  "NUEVA EMPRESA S.L." (2 rows)                            │
│     [ + Create security ]  [ Map to existing ]               │
│                                                              │
│  ────────────────────────────────────────────────────────── │
│  ⚠️  {C} companies unresolved — {R} rows cannot be imported   │
│     until all companies are resolved.                        │
│                                                              │
│  ☑ Save accepted mappings as aliases for future imports      │
│                                                              │
│  [ ← Back ]                              [ Continue → ]     │
│  (Continue enabled only when C = 0)                          │
└──────────────────────────────────────────────────────────────┘
```

**"Continue" is gated:** The user cannot proceed to Preview until ALL companies are resolved. This enforces the directive that unresolved rows are never ingested.

**"Create security" inline form:**
```
  Exchange / Market  [XNYS ▾ | XMAD | XAMS | XLON | XSWX | Other: ___]
  Ticker symbol      [text, required]
  Display name       [pre-filled from company_raw]
  Listing currency   [pre-filled from batch source currency]
  ISIN               [text, optional]
  
  [ Cancel ]  [ Create & map ]
```

Creates a `security` document in `_global` and auto-maps the company name as an alias.

**"Map to existing" flow:** Opens a search/combobox over all existing securities. User selects; mapping saved as alias.

### 4.4 Alias Persistence

When the user resolves a company name (via AI confirmation, manual mapping, or security creation), the alias is added to the target security's `aliases[]` array. Future imports auto-resolve deterministically (Step 1) without AI involvement.

Aliases are stored on the security document itself — no separate `security_alias_map` document needed. This simplifies queries: to check if a normalized name resolves, query `SELECT * FROM c WHERE c.doc_type = 'security' AND ARRAY_CONTAINS(c.aliases, @normalized_name)` against `_global`.

### 4.5 Impact on `_unassigned` Broker

The directive confirms: **security is required, account remains optional.**

| Attribute | Required at import? | Effect if absent |
|-----------|-------------------|------------------|
| Security (`security_id`) | **YES** | Row is BLOCKING — cannot import |
| Account (`account_id`) | No | Stored in `_unassigned` partition; no warning |

This is unchanged from the prior import architecture — `ERROR_UNRESOLVED_SECURITY` was already blocking. The new directive adds the AI suggestion pipeline and the explicit "resolve ALL before proceeding" gate.

---

## 5. Updated Import Warning/Error Taxonomy

Changes from the prior consolidated import design (`danny-dividend-csv-import-consolidated.md`):

### 5.1 `UNRESOLVED_SECURITY` — Strengthened

**Prior:** Blocking error in preview; alias mapper available inline.  
**Now:** Blocking; resolved in dedicated Step 2 BEFORE preview. Rows with unresolved company never appear in the preview table at all. The preview shows only rows with confirmed `security_id`.

### 5.2 `WARNING_RIGHTS_PENDING` — Unchanged

Still a non-blocking WARNING with visible badge on all years. The security for the rights row must be resolved (blocking), but the rights classification itself remains a post-import reconciliation task (non-blocking).

### 5.3 Full Taxonomy (Updated)

**BLOCKING (🔴) — resolved before preview or excluded from import:**

| Code | Trigger | Resolution |
|------|---------|------------|
| `ERROR_UNRESOLVED_SECURITY` | Company not mapped to a security | Step 2: alias match, AI suggestion, or create/map |
| `ERROR_PARSE_FAILURE` | Unparseable column value | Fix source data |
| `ERROR_MISSING_DATE` | Date column blank | Fix source data |
| `ERROR_NEGATIVE_AMOUNT` | Any monetary column < 0 | Fix source data |
| `ERROR_ALL_ZERO` | All monetary columns = 0 | Excluded (artifact) |
| `ERROR_INTRA_FILE_DUPLICATE` | Same normalized_row_hash twice in batch | Fix source data |

**WARNING (⚠️) — imports with visible badge, user action recommended:**

| Code | Trigger | Persists until |
|------|---------|----------------|
| `WARNING_RIGHTS_PENDING` | `Importe en Derechos > 0` (dividends) | Reconciliation complete |
| `WARNING_PENDING_SCRIP_CLASSIFICATION` | Zero-cost purchase row (purchases) | User classifies |
| `WARNING_ARITHMETIC_MISMATCH` | Amounts don't reconcile within tolerance | User confirms/corrects |
| `WARNING_YEAR_DATE_MISMATCH` | Year column ≠ payment/trade date year | User confirms |
| `WARNING_POSSIBLE_DUPLICATE` | Cross-batch hash match | User confirms or voids |
| `WARNING_WITHHOLDING_EXCEEDS_GROSS` | WHT > gross (dividends) | User corrects |
| `WARNING_AMBIGUOUS_NUMBER` | Number parse heuristic uncertain | User confirms |
| `WARNING_INVENTORY_SHORTFALL` | SELL qty > known holdings at date (sales) | User links lots |

**NOT warnings:**
- Missing account → administrative, no badge
- AI suggestion pending → resolved in Step 2, never reaches preview

---

## 6. Interaction with Current Options System

### 6.1 No Destructive Changes

The existing `symbols` container, its `symbol_config` documents, and all options APIs/routes continue to operate exactly as they do today. No renames, no schema changes, no partition key changes.

### 6.2 The Bridge: `legacy_symbol`

Each canonical security document carries a `legacy_symbol` field — the bare ticker string that matches the existing `symbol_config` in the `symbols` container.

```
security:    { security_id: "XNYS:AAPL", legacy_symbol: "AAPL" }
                                              │
symbols container:  { id: "config_AAPL", symbol: "AAPL", ... }
```

**Read path (portfolio → options):** When the Portfolio Holdings page needs to show whether a security has active options positions, it reads the `legacy_symbol`, queries `symbols` container for `config_{legacy_symbol}`, and checks `positions[]`.

**Read path (options → portfolio):** When the options system needs to know how many shares the user holds (for covered call contract calculation), it reads the security's `legacy_symbol`, finds the canonical `security_id`, then queries portfolio movements for that `security_id` to derive holdings. This replaces the mutable `total_shares` field over time.

**Write path:** Each system writes ONLY to its own container. The portfolio system never writes to `symbols`; the options system never writes to `portfolio`. Cross-references are read-only.

### 6.3 `total_shares` Transition

The mutable `total_shares` on `symbol_config` is used by ~332 references across 45 files (options screening, best_options, contract validation). It cannot be replaced overnight.

**Transition plan:**

| Phase | `total_shares` behavior | Portfolio holdings |
|-------|------------------------|-------------------|
| Phase 1 (MVP) | Remains mutable, editable in Symbols UI. Unchanged. | Derived from movements. Parallel, no cross-reference. |
| Phase 1b (Import) | Unchanged. | Populated via CSV import. Holdings now has data. |
| Phase 2 (Reconciliation) | Read-only notice: "Edit in Portfolio to keep accurate". Link to Portfolio Movements. | Full ledger available. Reconciliation tool compares `total_shares` vs. derived holdings. |
| Phase 3 (Derivation) | Computed from portfolio ledger. Read-only in Symbols UI. `total_shares = portfolio.derive_holdings(security_id).quantity`. | Authoritative source. |
| Phase 4+ (Cleanup) | Field deprecated. Backend reads from portfolio directly. | Single source of truth. |

**Phase 2 reconciliation tool:** Shows `| Symbol | total_shares (mutable) | Portfolio Holdings (derived) | Delta |` for each security present in both systems. User can "sync" individual rows (sets `total_shares` = derived) or "sync all". This is a one-way operation: portfolio is authoritative; `total_shares` catches up.

---

## 7. Migration & Compatibility

### 7.1 Phase M1 — Seed Security Master from Existing Symbols

A one-time migration script reads all `symbol_config` documents from the `symbols` container and creates corresponding `security` documents in `portfolio._global`:

```
For each symbol_config:
  security_id = infer_exchange_mic(symbol, exchange) + ":" + symbol
  Create security document:
    exchange_mic = inferred from symbol_config.exchange
    ticker = symbol_config.symbol
    display_name = symbol_config.display_name
    listing_currency = inferred from exchange
    legacy_symbol = symbol_config.symbol
    states.agent_tracking = symbol_config.watchlist
    aliases = [normalize(display_name)]
    isin = null  (populated later by enrichment or user)
```

**Exchange MIC inference:** The existing `exchange` field on `symbol_config` contains strings like `"NYSE"`, `"NASDAQ"`, etc. A mapping table converts these to ISO MICs:

| Current `exchange` | MIC |
|-------------------|-----|
| NYSE | XNYS |
| NASDAQ | XNAS |
| (new — user-created) | User selects MIC at creation |

For securities with unknown exchange, a placeholder `XXXX` MIC is used with `WARNING_EXCHANGE_UNRESOLVED` for the user to fix.

### 7.2 Phase M2 — Add `security_id` to symbol_config

Add a `security_id` field to each `symbol_config` document in the `symbols` container. This is a non-breaking additive change — existing code ignores unknown fields.

```json
// Before
{ "id": "config_AAPL", "symbol": "AAPL", "exchange": "NYSE", ... }

// After (additive)
{ "id": "config_AAPL", "symbol": "AAPL", "exchange": "NYSE",
  "security_id": "XNYS:AAPL", ... }
```

This enables future code to cross-reference the canonical security without a lookup.

### 7.3 Phase M3 — API Compatibility Layer

New portfolio APIs accept `security_id` as the primary identifier. A thin compatibility layer translates:

```python
# New canonical path
GET /api/portfolio/securities/XNYS:AAPL/holdings

# Compatibility: old ticker-based path still works
GET /api/symbols/AAPL  →  unchanged, reads from symbols container
GET /api/portfolio/holdings?symbol=AAPL  →  resolves legacy_symbol → security_id, then queries
```

The existing `/api/symbols/*` routes are NEVER modified. They continue to read from the `symbols` container. New portfolio routes read from `portfolio` container and reference canonical `security_id`.

---

## 8. UI Transition — Symbols/Watchlist → Securities/Portfolio

### 8.1 Decision: Yes, Eventually Rename — But Not Now

**Prior decision:** "Do NOT rename Symbols or Watchlist."  
**New decision:** Plan a phased UI rename, but DO NOT execute it in Phase 1 or 1b. Execute in Phase 2 or later, when portfolio has real data and the unified experience is tangible.

**Rationale for eventual rename:**
- The user's directive explicitly asks for a unified catalogue. Having "Symbols" for options and "Portfolio" for ownership is a split that doesn't match the unified model.
- "Securities" is the correct domain term for the canonical master.
- "Watchlist" is a subset behavior (agent tracking), not an entity type.

**Target end-state navigation:**

```
Securities ▾           Portfolio ▾         Economics    Chat    Screener ▾    Settings ▾
├─ All Securities      ├─ Holdings
├─ Agent Tracking      ├─ Movements
│   (was "Watchlist")  ├─ Dividends
├─ Calendar            ├─ Accounts
└─ Action Plans        └─ Import
```

### 8.2 Phased Transition

| Phase | Navigation | Notes |
|-------|-----------|-------|
| Phase 1 (MVP) | **Symbols** (unchanged) + **Portfolio** (new, alongside) | Two top-level menus. No rename. |
| Phase 1b (Import) | Same | Import lives under Portfolio. |
| Phase 2 (Reconciliation) | **Symbols** shows "Also see: Portfolio Holdings" link. **Portfolio Holdings** shows options position indicator (via bridge). | Cross-references visible but menus unchanged. |
| Phase 3 (Rename) | **Securities** replaces Symbols. Subpages reorganized. | URL redirect: `/symbols` → `/securities`. All `/symbols/*` routes 301 redirect. |
| Phase 3+ | **Securities** fully unified. "Agent Tracking" is a filter/view, not a separate concept. | `symbol_config` documents may be migrated into `security` documents (or kept as operational data in `symbols` container with canonical identity from security master). |

### 8.3 Route Compatibility

When the rename happens (Phase 3), a Next.js middleware redirect ensures no bookmarks break:

```
/symbols          → /securities
/symbols/calendar → /securities/calendar
/symbols/[symbol] → /securities/[securityId]  (resolve via legacy_symbol)
/plans            → /securities/plans
```

The backend `/api/symbols/*` routes remain permanently — they serve the options system. Only the frontend navigation labels change.

---

## 9. Security Resolution for All Import Types

The security resolution step (§4) applies uniformly to ALL import types:

| Import type | Company column | Resolution behavior |
|-------------|---------------|-------------------|
| Dividends (8 cols) | `Empresa` (col 1) | Full pipeline: alias → ticker → AI → create/map |
| Purchases (7 cols) | `Empresa` (col 1) | Same pipeline. Zero-cost rows still require resolved security. |
| Sales (6 cols) | `Empresa` (col 1) | Same pipeline. |

The alias database is shared across import types — resolving "APPLE INC" for a dividend import also resolves it for a purchase or sale import.

---

## 10. MVP Backlog — Security Master Slices

These slices integrate with the existing Phase 1 and Phase 1b backlogs.

| Slice | Title | Phase | Acceptance |
|-------|-------|-------|------------|
| S-SEC-1 | Security document schema & CRUD API | Phase 1 | `POST/GET/PATCH/DELETE /api/portfolio/securities`. Schema per §2.3. Stored in `portfolio._global`. Validation: unique `security_id`, valid MIC format, ticker required. |
| S-SEC-2 | Seed migration script | Phase 1 | One-time script reads all `symbol_config` from `symbols` container, creates `security` docs in `portfolio._global`. Exchange→MIC mapping. Idempotent (re-run safe). |
| S-SEC-3 | Alias resolution engine | Phase 1b | Given a normalized company name, returns: exact alias match (auto-resolve), ticker match (auto-resolve), or unresolved. No AI in this slice. |
| S-SEC-4 | AI suggestion endpoint | Phase 1b | `POST /api/portfolio/securities/suggest` — accepts list of unresolved names + known securities, returns ranked suggestions with confidence. AI never auto-confirms. |
| S-SEC-5 | Import wizard Step 2 | Phase 1b | Security resolution page per §4.3. Auto-resolved shown green. AI suggestions need click. Unresolved need create/map. "Continue" gated on all resolved. |
| S-SEC-6 | `legacy_symbol` bridge queries | Phase 1 | Helper functions: `get_security_by_legacy_symbol(ticker)`, `get_symbol_config_for_security(security_id)`. Cross-container read-only. |
| S-SEC-7 | `security_id` backfill on symbol_config | Phase 2 | Migration script adds `security_id` field to existing `symbol_config` docs. Non-breaking. |
| S-SEC-8 | Reconciliation tool (total_shares vs derived) | Phase 2 | UI showing comparison table. "Sync" button per security. |
| S-SEC-9 | UI rename Symbols → Securities | Phase 3 | Navigation label change, route redirects, no backend changes. |

---

## 11. Acceptance Criteria — Security Master

### 11.1 Identity

- [ ] Every security has a unique `security_id` in format `{MIC}:{TICKER}`
- [ ] Ticker alone does NOT identify a security — MIC is required
- [ ] ISIN is optional; when present, validated as 12-char ISO 6166 format
- [ ] `legacy_symbol` bridges to existing `symbols` container `symbol_config`
- [ ] `aliases[]` array stores normalized company name variants for import auto-resolution

### 11.2 Orthogonal States

- [ ] A security can exist with zero holdings (not an error)
- [ ] A security can have agent tracking flags without being owned
- [ ] A security can be owned without agent tracking
- [ ] `archived = true` hides from default views but does not delete
- [ ] Holdings are NEVER stored on the security document — always derived from ledger

### 11.3 Import Gating

- [ ] Import Step 2 resolves ALL company names before preview
- [ ] Deterministic alias/ticker matches auto-resolve (no user click)
- [ ] AI fuzzy suggestions require explicit user confirmation click
- [ ] Unresolved companies prevent proceeding to preview
- [ ] "Create security" inline form available for genuinely new securities
- [ ] Alias saved to security's `aliases[]` on confirmation
- [ ] Security resolution shared across dividend/purchase/sale imports

### 11.4 Options Compatibility

- [ ] Existing `/api/symbols/*` routes unchanged
- [ ] `symbol_config` documents unchanged in Phase 1
- [ ] `total_shares` remains mutable in Phase 1
- [ ] `legacy_symbol` enables cross-reference without code changes to options system
- [ ] No existing test broken by security master addition

---

## 12. Open Decisions Requiring User Input

| # | Question | Recommended Default | Impact |
|---|----------|-------------------|--------|
| U-SEC-1 | Confirm initial exchange MIC mappings for existing symbols. All current symbols presumed US (XNYS/XNAS)? | Yes — seed migration maps all to XNYS unless exchange field says "NASDAQ" → XNAS | One-time migration script |
| U-SEC-2 | Should the "Create security" form during import require exchange MIC selection, or default to batch currency heuristic? | Require selection — prevents silent miscategorization. Pre-select based on batch currency (EUR→XMAD, USD→XNYS). | UX for Step 2 |
| U-SEC-3 | AI suggestion model: use existing app's LLM (agent framework) or lightweight string similarity? | Phase 1b: string similarity (Levenshtein + token overlap). Phase 2: LLM if string similarity insufficient. | Implementation complexity |
| U-SEC-4 | When to execute the UI rename (Phase 3)? After portfolio has N securities, after X months, or user-triggered? | After Phase 2 reconciliation tool is live and user has used it. | Timeline only |

---

## 13. Corrections to Prior Decisions

| Prior Decision | Statement | Correction |
|---------------|-----------|------------|
| Decision #1 §Domain Boundaries | "Keep `symbol_config` as-is for options; portfolio movements independent. Link via ticker symbol (string), not foreign key." | **Superseded.** Link via `security_id` (canonical), not bare ticker. `legacy_symbol` provides backward compatibility. |
| Decision #1 §Domain Boundaries | "Do NOT rename Symbols or Watchlist — avoids 40+ file churn with zero functional gain." | **Superseded.** Plan rename for Phase 3. Defer execution, not the decision. The unified model requires unified naming eventually. |
| Decision #1 §Security Identity | "Primary identifier: ISIN (ISO 6166, 12-character). All transactions must carry ISIN." | **Superseded.** Primary identifier is `security_id` (`MIC:TICKER`). ISIN is optional, populated when known. Transactions carry `security_id` (required) + ISIN (optional). |
| Import consolidated §RC-6 | `ERROR_UNRESOLVED_SECURITY`: "Company name not mapped to a known security (resolvable via alias mapper before import)" | **Strengthened.** Now resolved in dedicated Step 2 with AI assistance. Unresolved rows never reach preview. |
| Import consolidated §7 | "Alias mapping: stored as `security_alias_map` in `portfolio._global`" | **Superseded.** Aliases stored directly on security document's `aliases[]` array. No separate alias map document. |

---

## 14. Amendment — Inline Security Creation & Single Canonical Record

**Superseding note (2026-09-05T17:27):** This section incorporates directive `copilot-directive-20260905T172200+0200.md`. It strengthens §3.2 and §4, supersedes the brief "Create security" form in §4.3, and clarifies the relationship between the canonical `security` document and the existing `symbol_config`.

### 14.1 One Canonical Record — No Duplicates

**Prior design ambiguity:** §3.2 showed a canonical `security` doc in `portfolio._global` AND an unchanged `symbol_config` in `symbols`. This could be read as two parallel identity records for the same equity. That is incorrect.

**Authoritative rule:** There is exactly ONE canonical identity record per security — the `security` document in `portfolio._global`. The `symbol_config` in the `symbols` container is **operational state** (options positions, agent run history, enrichment data), not a security identity record. It references the canonical security via `security_id` (after Phase M2 backfill) or `legacy_symbol` (bridge).

**Consequences:**

| Question | Answer |
|----------|--------|
| "Does AAPL exist in the system?" | Check `portfolio._global` for a `security` doc with `security_id = "XNYS:AAPL"`. Canonical. |
| "Does AAPL have options tracking enabled?" | Read `security.states.agent_tracking` on the canonical doc. Authoritative source. |
| "Does AAPL have options positions?" | Read `symbol_config.positions[]` from `symbols` container (via `legacy_symbol` bridge). Operational data lives in `symbols`. |
| "How many shares of AAPL do I own?" | Derive from portfolio ledger movements where `security_id = "XNYS:AAPL"`. Never stored. |
| "Can I create AAPL for portfolio without it being in the watchlist?" | Yes. A `security` doc exists independent of any `symbol_config`. |
| "Can I import dividends for a security that has no `symbol_config`?" | Yes. Import only needs the canonical `security` doc. |

**Creating a security during import does NOT create a `symbol_config`.** A `symbol_config` is created only when the user enables agent tracking for that security (via Symbols/Watchlist UI or via the future Securities page). This ensures no duplicate identity records.

**Existing `symbol_config` records are preserved.** The migration script (§7.1) creates canonical `security` docs FROM existing `symbol_config` records. After migration, both documents exist but serve different purposes — one for identity (security), one for operations (symbol_config).

### 14.2 Inline Security Creation During Import Chat — Detailed Specification

When the import chat's company resolution identifies an unresolved company (no alias match, no accepted AI suggestion), the user can create a new security inline without leaving the conversation.

#### Minimal Required Fields

| Field | Required | Validation | Default / Pre-fill |
|-------|----------|-----------|-------------------|
| **Ticker** | Yes | 1–10 chars, alphanumeric + `.` (for BRK.B etc.) | Extracted from company name if ticker-like (e.g., "MSFT" → "MSFT") |
| **Exchange / MIC** | Yes | Must be in known MIC list (ISO 10383 operating MICs) | Pre-selected from batch currency heuristic: EUR→XMAD, USD→XNYS, GBP→XLON, CHF→XSWX. User must confirm. |
| **Trading currency** | Yes | `EUR \| USD \| GBP \| CHF` | Pre-filled from batch `source_currency` |
| **Display name** | Yes | Non-empty string | Pre-filled from `empresa_raw` (original company name from CSV) |

#### Optional Fields

| Field | Validation | Notes |
|-------|-----------|-------|
| **ISIN** | 12-char ISO 6166 format if provided | Not required for import; valuable for Phase 4 tax export |
| **Country of domicile** | ISO 3166-1 alpha-2 | Useful for withholding rate defaults |
| **Broker IDs** (CUSIP, SEDOL, conid) | Format-validated per type | For broker-specific reconciliation |
| **Asset class** | `equity \| etf \| reit \| bond \| preferred` | Default: `equity` |

#### Pre-Creation Checks (Deterministic, Before Confirmation)

Before the user confirms creation, the system runs these checks and reports results:

| Check | Trigger | Behavior |
|-------|---------|----------|
| **Exact `security_id` collision** | `{MIC}:{TICKER}` already exists in `_global` | 🔴 BLOCK creation. Show existing security: "XMAD:ENG already exists as 'Enagás S.A.' — did you mean to map to it instead?" Offer [Map to existing] or [Edit ticker/exchange]. |
| **Same ticker, different MIC** | Ticker exists but on another exchange | ⚠️ WARNING: "ENG also exists on XLON (Enagás London listing). Are these the same company?" Offer [Map to existing] or [Create separate]. |
| **ISIN collision** (if ISIN provided) | ISIN matches an existing security | ⚠️ WARNING: "ISIN US1234567890 is already assigned to XNYS:XYZ. Creating a second security with the same ISIN is unusual." Offer [Map to existing] or [Create anyway]. |
| **Alias collision** | `normalize(display_name)` matches an alias on another security | ⚠️ WARNING: "The name 'Enagás' is already an alias for XMAD:ENA. Is this the same company?" Offer [Map to existing] or [Create with different name]. |

All checks are deterministic queries against `_global`. No AI involved. The LLM presents the results; the user decides.

#### Confirmation Flow

```
User: It's Enagás, trades as ENG on Madrid.

Agent: I'll create a new security:
  ┌─────────────────────────────────────────────┐
  │  Create Security                             │
  │  Ticker:       ENG                           │
  │  Exchange:     XMAD (Bolsa de Madrid)        │
  │  Currency:     EUR                           │
  │  Display name: Enagás S.A.                   │
  │  ISIN:         (none)                        │
  │  security_id:  XMAD:ENG                      │
  │                                              │
  │  ✅ No collisions found.                      │
  │                                              │
  │  [ ✓ Create ]  [ Edit ]  [ Cancel ]          │
  └─────────────────────────────────────────────┘

User: Create.

Agent: ✅ Created XMAD:ENG (Enagás S.A.).
  Saved "ENAGAS SA" as alias.
  7 rows now resolved → revalidating...
  ✅ 7 rows passed validation. 0 remaining unresolved companies.
```

#### Post-Creation Automatic Propagation

When a security is created:

1. **Alias saved:** `normalize(empresa_raw)` added to the new security's `aliases[]`. This is the cross-session memory — next import auto-resolves.
2. **All matching rows updated:** Every row in the current import session with the same normalized company name gets `security_id` set to the new value.
3. **Batch revalidated:** `validate_batch` re-runs for the affected rows. Warnings that depended on missing security (if any) are re-evaluated.
4. **Pending questions updated:** `COMPANY_RESOLVE_UNMATCHED` question for this company moves to `ANSWERED`. If this was the last unresolved company, the summary may show "All companies resolved — ready for final preview."

Steps 2–4 are deterministic (performed by the validation engine, not the LLM). The LLM reports the outcome.

#### Failure Behavior & Session Preservation

| Failure | State after failure | Recovery |
|---------|-------------------|----------|
| Cosmos write fails (network, throttle) | Session unchanged; no security created; company remains UNRESOLVED | Agent says: "Creation failed (network error). Your session is saved. Try again?" Retry is safe (idempotent check on `security_id`). |
| Collision detected pre-creation | Creation blocked by check; session unchanged | Agent presents collision details; user maps to existing or edits fields. |
| User cancels mid-form | Session unchanged; company remains UNRESOLVED | Agent continues with other questions or offers to retry. |
| Browser closes after creation but before revalidation | Security exists in Cosmos; session `resolution_state` may be stale | On resume, `resolve_companies` re-runs; the new security's alias matches the company name; auto-resolves deterministically. No data loss. |

**Key invariant:** The `import_session` document is never in an inconsistent state. Security creation and session state update are independent operations. If creation succeeds but session update fails, the next resume auto-resolves via alias match. If creation fails, the session still has the company as UNRESOLVED.

### 14.3 Compatibility Adapters — `/symbols` API Preservation

**Principle:** The existing `/api/symbols/*` routes continue to work exactly as today. They read from and write to the `symbols` container. No adapter needed for existing operations.

**New behavior added by adapters (Phase 2+):**

| Operation | Current behavior | With adapter |
|-----------|-----------------|-------------|
| `GET /api/symbols/AAPL` | Returns `symbol_config` from `symbols` container | **Same** — unchanged. Optionally enriched with `security_id` from canonical doc (after Phase M2). |
| `POST /api/symbols` (create symbol) | Creates `symbol_config` in `symbols` | **Same** + also creates/updates canonical `security` in `portfolio._global` if not present. This ensures the canonical catalog stays in sync when users add symbols through the existing UI. |
| `PATCH /api/symbols/AAPL/watchlist` | Updates watchlist flags on `symbol_config` | **Same** + syncs `states.agent_tracking` on canonical security doc. |
| `DELETE /api/symbols/AAPL` | Archives/removes `symbol_config` | `symbol_config` archived. Canonical `security` doc is NOT deleted (it may be referenced by portfolio movements). |

**Phase 1 (no adapters):** Both systems operate independently. The seed migration (§7.1) creates canonical security docs from existing symbol_configs.

**Phase 2 (adapters added):** Write operations to `/api/symbols` propagate to the canonical security. Read operations optionally enrich from the canonical security. This is additive — no existing behavior changes.

**Phase 3+ (unified):** New Securities UI manages the canonical doc directly. `/api/symbols` routes become thin compatibility shims that still work for any external consumers or bookmarked URLs.

### 14.4 Schema for Shared Consumers

| Consumer | Reads from | Writes to | What it needs |
|----------|-----------|-----------|--------------|
| Portfolio import | `security` in `_global` | `security` (creation), movements in `{account_id}` | `security_id`, aliases, display_name |
| Portfolio holdings | movements in `{account_id}`, `security` in `_global` | (derived, no writes) | `security_id`, display_name, listing_currency |
| Watchlist / Agent toggles | `security.states.agent_tracking` | `security.states.agent_tracking` (via adapter or directly) | `security_id`, agent_tracking flags |
| Options cache | `symbol_config.positions[]` in `symbols` | `symbol_config` in `symbols` | `legacy_symbol` bridge to canonical security |
| Symbol detail page | `symbol_config` + activities/alerts/reports in `symbols` | (read-only) | `legacy_symbol` → `security` for canonical identity |
| Options screener | `symbol_config` via `list_symbols()` | (read-only) | `total_shares` (Phase 1-2), derived holdings (Phase 3+) |

### 14.5 Updated Backlog Slices

S-SEC-5 in §10 referenced the wizard Step 2, which is superseded by the chat-based import. Updated:

| Slice | Title | Phase | Acceptance (updated) |
|-------|-------|-------|---------------------|
| S-SEC-1 | Security document schema & CRUD API | Phase 1 | **Updated:** includes pre-creation collision checks (exact `security_id`, same ticker other MIC, ISIN, alias). `POST /api/portfolio/securities` runs all checks before write. |
| S-SEC-5 | ~~Import wizard Step 2~~ → Chat inline creation | Phase 1b | **Replaced:** Security creation happens inside the import chat (§14.2). `create_security` tool with minimal fields, collision checks, confirmation card. Post-creation auto-propagation to all matching rows + revalidation. Session-safe on failure. |
| S-SEC-10 | Compatibility adapters (new) | Phase 2 | Write adapters on `POST /api/symbols` and `PATCH /api/symbols/*/watchlist` to sync canonical security doc. Read enrichment on `GET /api/symbols/*`. |

### 14.6 Updated Acceptance Criteria

Append to §11:

**11.5 Inline Creation (Import Chat)**

- [ ] Unresolved company in import chat offers "Create security" action
- [ ] Minimal required fields: ticker, exchange MIC, trading currency, display name
- [ ] ISIN and broker IDs optional
- [ ] Pre-creation checks run: `security_id` collision, same-ticker-other-MIC, ISIN collision, alias collision
- [ ] Collision blocks creation and shows existing security with "Map to existing" option
- [ ] User must confirm creation (explicit click or natural-language "yes")
- [ ] Post-creation: alias saved, all matching rows updated, batch revalidated, questions updated
- [ ] Revalidation is deterministic (not LLM)
- [ ] Creation failure preserves session intact; company remains UNRESOLVED; retry safe
- [ ] Browser close after creation + before session update → resume auto-resolves via alias
- [ ] Created security has NO `symbol_config` counterpart (unless user later enables agent tracking)
- [ ] Created security is visible in all catalog queries (list_securities, search_securities)

**11.6 Single Canonical Record**

- [ ] ONE `security` doc per equity in `portfolio._global` — no Portfolio-only duplicates
- [ ] `symbol_config` is operational state (options positions), not a security identity
- [ ] Security existence is independent of ownership AND independent of watchlist membership
- [ ] A security with zero holdings and no agent tracking is valid (not archived automatically)
- [ ] Creating a security during import does NOT create a `symbol_config`
- [ ] Enabling agent tracking on a security MAY create a `symbol_config` (Phase 2 adapter)

**11.7 Compatibility**

- [ ] `GET /api/symbols/AAPL` returns same payload as today
- [ ] Phase 2 adapter: `POST /api/symbols` also upserts canonical security
- [ ] Phase 2 adapter: `PATCH /api/symbols/*/watchlist` syncs `states.agent_tracking`
- [ ] `DELETE /api/symbols` does NOT delete canonical security (it may have portfolio movements)
- [ ] All existing tests pass without modification in Phase 1

---

*End of unified security master recommendation (with amendment §14). No production code. Implementation of S-SEC-1 and S-SEC-2 should be the first slices in Phase 1 (before any movement creation), as all subsequent portfolio features depend on the canonical security master existing.*
