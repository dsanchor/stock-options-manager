# Security Master — Final Container & Identity Resolution

**Author:** Danny (Lead/Architecture)
**Date:** 2026-09-05
**Status:** AUTHORITATIVE — final resolution; supersedes conflicting statements in prior documents.

**Supersedes (on the specific points resolved here):**
- `danny-unified-security-master.md` §2.1 (`security_id = MIC:TICKER`) — **format retained**; Livingston's `TICKER:MIC` overruled
- `danny-unified-security-master.md` §2.3, §3.2 (storage in `portfolio._global`) — **superseded** by `symbols` container
- `danny-unified-security-master.md` §14.1 ("check `portfolio._global`") — **superseded** by `symbols` container
- `danny-chat-import-architecture.md` §3.1 (`import_session` in `portfolio._global`) — **superseded** by `import_sessions` container
- `livingston-security-identity-import-matching.md` §3.1 (`security_id = TICKER:MIC`) — **overruled** by `MIC:TICKER`
- `livingston-security-identity-import-matching.md` §9 (staged rows in `portfolio_ledger`) — **normalized** to `portfolio`
- Any reference to `portfolio_ledger` as a container name — **normalized** to `portfolio`
- `danny-unified-security-master.md` §10 slice S-SEC-1 ("Stored in `portfolio._global`") — **superseded** by `symbols` container

**Preserves unchanged:**
- All prior decisions on FX convention, null-vs-zero WHT, scrip `ca_event`/`ca_leg` model, cost-basis methods, warning taxonomy, chat-import orchestration, question queue, no-hidden-writes rule, holdings derivation, `total_shares` transition plan
- Livingston's schema for `security_master` document (§4), alias model (§8), 5-stage matching pipeline (§7), inline creation 4-step write protocol (§10), corporate action lifecycle (§11)
- Livingston's `import_session` state machine (chat-import-state §3–§14), question scopes, commit protocol, concurrency/idempotency model

---

## R1. Container Decision: `symbols` (not `portfolio._global`)

### R1.1 Resolution

**The canonical `security_master` document lives in the `symbols` container, partitioned by the ticker portion of the security_id.**

This adopts Livingston's approach (`livingston-security-identity-import-matching.md` §3.2) and supersedes Danny's prior placement in `portfolio._global`.

### R1.2 Rationale

| Factor | `symbols` container (Livingston) | `portfolio._global` (Danny — superseded) |
|--------|----------------------------------|----------------------------------------|
| **Single-partition read with `symbol_config`** | ✅ `sec_XNYS:AAPL` and `config_AAPL` share partition `AAPL` — one partition read gets both | ❌ Cross-container point-read required to get `symbol_config` alongside security identity |
| **Existing infrastructure** | ✅ `symbols` is already provisioned, has service layer (`CosmosDBService`), error handling, and probes | ❌ Would require `portfolio` container to serve dual role: reference data + transactional ledger |
| **Schema separation** | `doc_type` filter cleanly separates `security_master` from `symbol_config` / `activity` / `alert` | ✅ Clean domain boundary — but achieved at the cost of a second identity locus |
| **Ticker collision within partition** | ✅ Handled: `SAN` on XMAD and XPAR both live in partition `SAN` with distinct `id` values (`sec_XMAD:SAN`, `sec_XPAR:SAN`) | ✅ All securities in `_global` — no partition-level collision. But all in one hot partition. |
| **Hot partition risk** | Low — securities distributed across ~100–200 ticker partitions | Medium — ALL securities in one `_global` partition (writes and reads concentrated) |
| **Cross-partition queries (ISIN, alias)** | Needed for ISIN collision checks. Mitigated by composite index `(doc_type, isin)` and `(doc_type, aliases.normalized)`. Acceptable at <500 securities. | Same — `_global` still requires index-based queries for alias/ISIN lookup |
| **Migration from existing `symbol_config`** | Additive: new doc type in same partition, same container. No cross-container writes needed during migration. | Requires creating docs in a different container, maintaining cross-container references indefinitely. |
| **Existing code impact** | Zero. Current code filters `doc_type = 'symbol_config'`; `security_master` documents are invisible to it. | Zero direct impact (different container). But import/portfolio code needs cross-container reads for security verification. |
| **Single-catalog guarantee** | ✅ One container holds all identity data. The `symbols` container IS the identity catalog. | ⚠️ Two containers hold identity-adjacent data. Even with "symbol_config is operational state" framing, the practical effect is split ownership. |

**Decisive factor:** The `symbols` container already IS the system's equity identity catalog. Adding `security_master` docs there is a natural evolution (additive, non-breaking). Placing them in `portfolio` would create a second identity locus — even if we declare one "canonical" and the other "operational," every query that needs both identity + operational state must cross containers. In `symbols`, a single-partition read returns both.

### R1.3 Partition Key Mechanics

The `symbols` container has partition key `/symbol`. For `security_master` documents:

```
security_master document:
  "symbol": "AAPL"        ← partition key (ticker portion of security_id)
  "id": "sec_XNYS:AAPL"  ← unique document ID
  "security_id": "XNYS:AAPL"
  "doc_type": "security_master"
```

**Co-location with existing documents:**

```
Partition "AAPL":
  id: "config_AAPL"       doc_type: symbol_config    (existing, unchanged)
  id: "sec_XNYS:AAPL"    doc_type: security_master  (new)
  id: "act_AAPL_..."      doc_type: activity         (existing, unchanged)

Partition "SAN":
  id: "config_SAN"        doc_type: symbol_config    (existing — US SAN if present)
  id: "sec_XMAD:SAN"     doc_type: security_master  (new — Santander Madrid)
  id: "sec_XPAR:SAN"     doc_type: security_master  (new — Sanofi Paris)

Partition "ULVR":          (new partition — portfolio-only, no symbol_config)
  id: "sec_XLON:ULVR"    doc_type: security_master  (new)
```

**Key observation:** A partition can have a `security_master` without a `symbol_config`. This is the portfolio-only case (security exists for dividend/ownership tracking but is not on the options watchlist). The partition key is the ticker, not dependent on `symbol_config` existence.

**Ticker collisions across exchanges** are handled naturally: two `security_master` documents with the same ticker but different MICs coexist in one partition with distinct `id` values. Queries filtering on `security_id` or `exchange_mic` disambiguate.

### R1.4 Impact on Existing Code

| Code path | Current behavior | After `security_master` addition |
|-----------|-----------------|----------------------------------|
| `cosmos_db.get_symbol("AAPL")` | Reads `config_AAPL` from partition `AAPL` | **Unchanged** — point-read by `id`, ignores other doc types |
| `cosmos_db.list_symbols()` | Queries `doc_type = 'symbol_config'` | **Unchanged** — filter excludes `security_master` |
| `cosmos_db.create_symbol(...)` | Creates `config_{symbol}` | **Unchanged** — writes `symbol_config` only |
| Any agent/options code | Reads `symbol_config` by ticker | **Unchanged** — no code touches `security_master` |

**Zero existing files modified.** The `security_master` documents are invisible to all current code paths.

### R1.5 What Moves Out of `portfolio`

With this resolution, the `portfolio` container holds ONLY transactional and account data:

```
portfolio container (pk /account_id):
├─ {account_id} partitions:
│   ├─ account (profile doc)
│   ├─ ledger_txn (BUY/SELL movements)
│   ├─ ca_event (DIVIDEND parent)
│   ├─ ca_leg (DIVIDEND sub-legs)
│   └─ staged_import_row (temporary, 90-day TTL)
├─ _unassigned partition:
│   └─ (movements with no assigned account)
├─ _global partition:
│   └─ import_batch (post-commit summary records)

symbols container (pk /symbol) — UNCHANGED structure, new doc type:
├─ {ticker} partitions:
│   ├─ symbol_config (existing, unchanged)
│   ├─ security_master (NEW — canonical identity)
│   ├─ activity, alert, report, etc. (existing, unchanged)
```

**No `_global` partition needed in `portfolio`** for security identity. The only `portfolio._global` documents are `import_batch` summaries (lightweight, infrequent). If even those feel misplaced, they can move to their respective account partitions.

### R1.6 Single Canonical Record Rule — Updated

The rule from §14.1 of the unified-security-master doc is preserved but the location changes:

> There is exactly ONE canonical identity record per security — the `security_master` document in the `symbols` container.

- `symbol_config` is operational state (options positions, agent results, enrichment data), NOT identity.
- Creating a `security_master` during import does NOT create a `symbol_config`.
- A `symbol_config` is created ONLY when the user enables agent/options tracking.
- "Does security X exist?" → query `symbols` container for `doc_type = 'security_master' AND security_id = X`.
- The embedded `security` snapshot in `portfolio` ledger records is a point-in-time copy, not a second catalog.

### R1.7 Corrections to Danny's Prior Design

| Prior statement (danny-unified-security-master) | Correction |
|------------------------------------------------|------------|
| §2.3: `"account_id": "_global"` on security document | **Removed.** Security docs use `"symbol": "{ticker}"` as partition key, not `/account_id`. |
| §3.2 diagram: `portfolio._global → security` | **Superseded.** `symbols.{ticker} → security_master` |
| §10 S-SEC-1: "Stored in `portfolio._global`" | **Superseded.** Stored in `symbols` container, partitioned by ticker. |
| §14.1: "Check `portfolio._global` for a `security` doc" | **Superseded.** Check `symbols` container for `security_master` doc. |
| §14.3: "creates/updates canonical `security` in `portfolio._global`" | **Superseded.** Creates/updates `security_master` in `symbols` container. |

---

## R2. Security ID Format: `MIC:TICKER` (not `TICKER:MIC`)

### R2.1 Resolution

**The canonical format is `{MIC}:{TICKER}` — MIC first, ticker second.**

```
XNYS:AAPL    not    AAPL:XNYS
XMAD:SAN     not    SAN:XMAD
XLON:ULVR    not    ULVR:XLON
```

This retains Danny's convention and overrules Livingston's `TICKER:MIC`.

### R2.2 Rationale

| Factor | `MIC:TICKER` (Danny — retained) | `TICKER:MIC` (Livingston — overruled) |
|--------|--------------------------------|--------------------------------------|
| **Namespace convention** | ✅ Broader scope first, like `namespace:identifier`. Standard in URIs, XML namespaces, TradingView (`NASDAQ:AAPL`). | Ticker first matches mental model of "search by ticker" but breaks namespace convention. |
| **Sort order** | Groups by exchange when sorted alphabetically: `XAMS:ASML, XAMS:SHELL, XLON:GSK, XLON:ULVR, XMAD:SAN, XNYS:AAPL` — useful for exchange-grouped UI views. | Groups by ticker: `AAPL:XNYS, ASML:XAMS, GSK:XLON...` — useful for ticker-alphabetical views. Exchange grouping requires secondary sort. |
| **Industry precedent** | TradingView: `NASDAQ:AAPL`. Bloomberg: exchange-qualified. Financial data APIs commonly use exchange-first. | Some broker UIs show ticker first (but they don't use a colon-separated compound key). |
| **URI path usage** | `/securities/XNYS:AAPL` reads as "within NYSE, Apple" — natural hierarchical path. | `/securities/AAPL:XNYS` reads as "Apple on NYSE" — also parseable but less hierarchical. |
| **Consistency with prior docs** | All Danny docs and consolidation examples use `MIC:TICKER`. Changing now would require updating 5+ decision documents. | Would require retroactive correction to all prior examples. |

**Decisive factors:**
1. **Namespace convention consistency:** Broader-scope-first is standard practice across URIs, DNS, package names, and financial data providers. `XNYS:AAPL` is analogous to `com.apple:aapl` or `NASDAQ:AAPL` in TradingView.
2. **Exchange-first sort for UI:** The portfolio spans 6+ exchanges. Grouping by exchange in sorted views is more useful than ticker-alphabetical (which mixes exchanges).
3. **Stability of existing documentation:** All prior architecture docs, acceptance criteria, conversation examples, and the user's own context use `MIC:TICKER`. Retroactive correction has no functional benefit but creates confusion.

### R2.3 Document ID Convention

The Cosmos document `id` uses underscores instead of colons (colons in Cosmos IDs can complicate certain SDK operations):

```
security_id: "XNYS:AAPL"       (canonical, used in all references)
document id: "sec_XNYS_AAPL"   (Cosmos storage, underscore separator)
```

The id-to-security_id mapping is deterministic: `id = "sec_" + security_id.replace(":", "_")`.

### R2.4 Livingston's Doc Update Required

Livingston's `livingston-security-identity-import-matching.md` uses `TICKER:MIC` throughout (e.g., `AAPL:XNAS`, `ULVR:XLON`, `IBE:XMAD`). The implementation must use `MIC:TICKER` (e.g., `XNAS:AAPL`, `XLON:ULVR`, `XMAD:IBE`). Specifically:

| Livingston reference | Corrected form |
|---------------------|----------------|
| `security_id = "{EXCHANGE_TICKER}:{ISO_10383_MIC}"` | `security_id = "{ISO_10383_MIC}:{EXCHANGE_TICKER}"` |
| `id: "sec_AAPL:XNAS"` | `id: "sec_XNAS_AAPL"` |
| `AAPL:XNAS` → `XNAS:AAPL` | All examples throughout |
| `KO:XNYS` → `XNYS:KO` | All examples throughout |
| `ULVR:XLON` → `XLON:ULVR` | All examples throughout |
| `IBE:XMAD` → `XMAD:IBE` | All examples throughout |

The partition key extraction changes correspondingly: `security_id.split(":")[1]` extracts the ticker for partition lookup (was `[0]` in Livingston's notation).

---

## R3. Import Session Container: `import_sessions` (dedicated, not `portfolio._global`)

### R3.1 Resolution

**Import sessions live in a dedicated `import_sessions` container with partition key `/session_id`.**

This adopts Livingston's approach (`livingston-chat-import-state.md` §2.1) and supersedes Danny's placement in `portfolio._global`.

### R3.2 Rationale

| Concern | `portfolio._global` (Danny — superseded) | `import_sessions` (Livingston — adopted) |
|---------|----------------------------------------|------------------------------------------|
| **TTL safety** | ❌ `portfolio` stores permanent ledger records. Enabling TTL on the container risks accidental deletion of ledger docs if `ttl` field is inadvertently set. | ✅ Dedicated container with TTL enabled (`defaultTtl = -1`). Only import session docs have TTL values. No ledger risk. |
| **Document size** | Session docs can reach 500KB+ (many questions, candidates). This bloats the `_global` partition alongside reference data. | ✅ Each session is its own partition. Uniform distribution. No hot partition. |
| **Write patterns** | Import sessions update frequently during conversation. Ledger records are append-mostly. Mixing patterns in one partition increases conflict probability. | ✅ Session writes are isolated. No contention with ledger writes. |
| **Indexing** | Session needs `(state, created_by, expires_at)`. Ledger needs `(account_id, security_id, date, type)`. Different index profiles in one container waste RUs. | ✅ Container-specific indexing. Light index for sessions; heavy index for ledger. |
| **Lifecycle** | Session TTL of 7 days; ledger is permanent. Mixing lifecycles in one container is an anti-pattern. | ✅ Clean lifecycle separation. |

**Container specification:**

```
Container: import_sessions
Partition key: /session_id
Default TTL: -1 (documents control their own TTL)
Document types: import_session, import_question, llm_call_record
```

### R3.3 Staged Import Rows

`staged_import_row` documents live in the `portfolio` container (not `import_sessions`), partitioned by the assigned `account_id` (default `_unassigned`). This is correct because:

1. Staged rows have 90-day TTL (different from 7-day session TTL).
2. They need to be in the same partition as the eventual ledger records for easy promotion on commit.
3. They hold parsed financial data at the same sensitivity level as ledger records.

**TTL implications for `portfolio` container:** The `portfolio` container must also have `defaultTtl = -1` (enabled, but documents control their own TTL). Only `staged_import_row` documents set `ttl` values. Permanent ledger records (`ledger_txn`, `ca_event`, `ca_leg`) do NOT set `ttl` — they live forever. This is safe because missing `ttl` = no expiry when `defaultTtl = -1`.

### R3.4 Cross-Container References

| From | To | How |
|------|------|-----|
| `import_session.entity_group[].resolved_security_id` | `security_master` in `symbols` | String reference; read at resolution time |
| `staged_import_row` (in `portfolio`) | `import_session` (in `import_sessions`) | Via `batch_id` / `session_id` string; read only for admin/debugging |
| `import_batch` (in `portfolio`) → `import_session` | `import_sessions` | Via `session_id` string; the batch doc records which session created it |

Cross-container references are string-based (no foreign keys in Cosmos). Each container is self-sufficient for its own read patterns.

---

## R4. Container Name Normalization: `portfolio` (not `portfolio_ledger`)

### R4.1 Resolution

**The transactional ledger container is named `portfolio`.** All references to `portfolio_ledger` in any design document are hereby normalized to `portfolio`.

Livingston's documents use `portfolio_ledger` in several places (staged rows, commit target, partition references). These must be read as `portfolio` during implementation.

### R4.2 Complete Container Inventory

After this resolution, the system has these containers:

| Container | Partition key | TTL | Purpose |
|-----------|--------------|-----|---------|
| `symbols` | `/symbol` (ticker) | None | Security identity (`security_master`), operational state (`symbol_config`), activity, alerts, reports |
| `portfolio` | `/account_id` | `-1` (doc-controlled) | Ledger transactions, CA events/legs, account profiles, staged import rows (90-day TTL), import batch summaries |
| `import_sessions` | `/session_id` | `-1` (doc-controlled) | Import session state machine (7-day TTL), questions, LLM call records |
| `telemetry` | (existing) | (existing) | Telemetry (existing, unchanged) |
| `settings` | (existing) | (existing) | App settings (existing, unchanged) |
| `dgi_screener` | (existing) | (existing) | DGI screener data (existing, unchanged) |
| `calendar` | (existing) | (existing) | Calendar events (existing, unchanged) |
| `agent_traces` | (existing) | (existing) | Agent trace logging (existing, unchanged) |

**New containers to provision:** `portfolio` and `import_sessions`. The `symbols` container exists; it gains new `security_master` documents (no provisioning change, possibly new composite indexes).

### R4.3 Required Index Additions on `symbols` Container

To support the `security_master` queries:

```
Composite index: (doc_type ASC, security_id ASC)
  → Enables: SELECT * FROM c WHERE c.doc_type = 'security_master' AND c.security_id = @id

Composite index: (doc_type ASC, isin ASC)
  → Enables: ISIN collision check (cross-partition by necessity; acceptable at <500 docs)

Included path: /aliases[]/normalized
  → Enables: alias lookup via cross-partition query
  → Alternative: in-memory index loaded from full catalog read (better for <200 securities)
```

---

## R5. Unified Naming: `security_master` (not `security`)

### R5.1 Resolution

**The doc_type is `security_master`, not `security`.**

Danny's docs used `doc_type: "security"`. Livingston used `doc_type: "security_master"`. This resolution adopts `security_master` for clarity:

- Distinguishes the canonical catalog document from the embedded `security` snapshot in ledger records.
- In code and queries, `doc_type = 'security_master'` is unambiguous. `doc_type = 'security'` could be confused with the embedded object.
- Consistent with Livingston's schema which is more detailed and will be the implementation reference.

### R5.2 Impact

All Danny documents that reference `doc_type: "security"` should be read as `doc_type: "security_master"` during implementation. Key locations:

- `danny-unified-security-master.md` §2.3: `"doc_type": "security"` → `"doc_type": "security_master"`
- `danny-unified-security-master.md` §3.2 diagram: `security` → `security_master`
- `danny-unified-security-master.md` §7.1: migration creates `security_master` docs
- `danny-chat-import-architecture.md` §3.2: "creates `security` documents" → "creates `security_master` documents"

---

## R6. Ticker-Only Route Compatibility & Collision Behavior

### R6.1 Current State

All existing routes use bare ticker strings:
- `GET /api/symbols/AAPL` → returns `symbol_config` with `id: "config_AAPL"`
- Options agents, watchlist, screener — all ticker-based

These routes MUST continue working unchanged.

### R6.2 Legacy Mapping: Ticker → security_id

When a component has only a ticker string and needs the canonical `security_id`:

**Case 1: Ticker has exactly one `security_master` in its partition.**
This is the common case for all existing US symbols. The mapping is unambiguous:
```
"AAPL" → single security_master in partition "AAPL" → security_id = "XNYS:AAPL"
```

**Case 2: Ticker has multiple `security_master` documents (cross-exchange collision).**
Example: `SAN` partition contains `sec_XMAD_SAN` (Santander) and `sec_XPAR_SAN` (Sanofi).

Resolution strategy:
1. If a `symbol_config` exists in the same partition (`config_SAN`), its `security_id` field (added in Phase M2) is the disambiguation. The `symbol_config` was created for a specific exchange; its `security_id` identifies which one.
2. If no `symbol_config` exists (portfolio-only securities), the ticker-only route returns a 300 Multiple Choices with the list of matching `security_master` documents. The caller must use a full `security_id`.
3. For Phase 1, collisions are unlikely — existing symbols are US-only. The migration script (Phase M1) creates one `security_master` per existing `symbol_config`. Non-US securities are added via portfolio/import flow which always uses full `security_id`.

### R6.3 Bridge Field: `legacy_symbol`

Every `security_master` document carries a `legacy_symbol` field — the bare ticker string that corresponds to the `symbol_config` in the same partition (if one exists):

```jsonc
{
  "security_id": "XNYS:AAPL",
  "legacy_symbol": "AAPL",       // matches symbol_config.symbol in same partition
  // ...
}
```

If no `symbol_config` exists (portfolio-only security), `legacy_symbol` is still set to the ticker string — it's the partition key and useful for queries regardless.

**Read path (portfolio → options):** Portfolio code reads `security_master.legacy_symbol`, uses it to point-read `config_{legacy_symbol}` from the SAME partition (single-partition, efficient).

**Read path (options → portfolio):** Options code reads `symbol_config.security_id` (after Phase M2 backfill), uses it to query `portfolio` container for movements.

### R6.4 Phase M2 Backfill

After security_master docs exist, a migration adds `security_id` to each `symbol_config`:

```jsonc
// Before (Phase 1)
{ "id": "config_AAPL", "symbol": "AAPL", "exchange": "NYSE", ... }

// After (Phase M2)
{ "id": "config_AAPL", "symbol": "AAPL", "exchange": "NYSE",
  "security_id": "XNYS:AAPL", ... }
```

This is additive and non-breaking. Existing code ignores unknown fields.

---

## R7. Schema Adoption

### R7.1 `security_master` Document Schema

**Livingston's schema (`livingston-security-identity-import-matching.md` §4) is adopted as the implementation reference**, with these modifications:

1. **`security_id` format:** `MIC:TICKER` (per R2), not `TICKER:MIC`.
2. **Document `id` format:** `sec_{MIC}_{TICKER}` (underscores, not colons).
3. **`symbol` field (partition key):** Set to the ticker portion of `security_id`.

All other fields from Livingston's schema are adopted as-is:
- `display_name`, `legal_name`, `short_name`
- `isin`, `cusip`, `sedol` (optional identifiers)
- `provider_symbols` (yfinance, tradingview, ibkr_conid)
- `aliases[]` with provenance (`raw`, `normalized`, `added_by`, `source`, `void`)
- `asset_class`, `country_of_incorporation`, `sector`
- `status` enum (ACTIVE, DELISTED, MERGED, SPINOFF_SOURCE, TICKER_CHANGED)
- `superseded_by`, `supersedes`, `corporate_action_note`
- `related_securities[]`
- `has_symbol_config`, `portfolio_tracked`

### R7.2 Danny's Schema Fields — Mapping

| Danny's field (§2.3) | Livingston's equivalent | Notes |
|----------------------|------------------------|-------|
| `states.agent_tracking` | `has_symbol_config` (flag) + actual tracking lives on `symbol_config` | Danny over-centralized agent tracking on security doc. Better: agent tracking stays on `symbol_config` where the agent code reads it. `has_symbol_config` is a boolean convenience flag, not the tracking config. |
| `states.archived` | `status: ACTIVE / DELISTED / ...` | Livingston's lifecycle enum is richer. Add `ARCHIVED` to the status enum for user-initiated archive (distinct from delisted). |
| `broker_ids` | `provider_symbols` + `cusip` / `sedol` top-level fields | Livingston's structure is better — top-level fields for standard IDs, `provider_symbols` for platform-specific IDs. |
| `legacy_symbol` | `has_symbol_config` flag + partition co-location | `legacy_symbol` as a field is still useful (see R6.3). Add it to Livingston's schema. Livingston didn't include it explicitly. |

### R7.3 Final Status Enum

```
ACTIVE | DELISTED | MERGED | SPINOFF_SOURCE | TICKER_CHANGED | ARCHIVED
```

`ARCHIVED` is added for user-initiated archive (sold all, no longer interested, but not delisted). Danny's `states.archived` boolean is superseded by this enum value.

---

## R8. Migration Script Updates

The seed migration script (Danny §7.1, Livingston §5.2) is updated for the new container:

```
For each existing symbol_config in `symbols` container:
  1. Infer exchange_mic from symbol_config.exchange (NYSE→XNYS, NASDAQ→XNAS, etc.)
  2. Construct security_id = "{exchange_mic}:{symbol}"
  3. Construct document:
     id: "sec_{exchange_mic}_{symbol}"
     symbol: symbol_config.symbol          (partition key — same partition)
     doc_type: "security_master"
     security_id: "{exchange_mic}:{symbol}"
     ticker: symbol_config.symbol
     exchange_mic: inferred
     display_name: symbol_config.display_name
     legacy_symbol: symbol_config.symbol
     has_symbol_config: true
     portfolio_tracked: false              (no ledger data yet)
     status: "ACTIVE"
     aliases: [{ raw: display_name, normalized: normalize(display_name), source: "DISPLAY_NAME_AUTO" }]
     ...
  4. Upsert into `symbols` container (same partition as existing symbol_config)
```

**Idempotent:** Re-running the script checks for existing `sec_{MIC}_{TICKER}` document by ID point-read before creating. If found, skips or updates only changed fields.

---

## R9. `SecurityCatalogService` — Sole Writer

Livingston's §5.4 principle is adopted: **`SecurityCatalogService` is the sole writer to `security_master` documents.** No other service, no import pipeline step, no LLM call path writes to `security_master`.

**Implementation location:** A new service class in `backend/src/`, alongside `cosmos_db.py`. It receives the `symbols` container client from `CosmosDBService` (or accesses it directly).

```python
# Conceptual — not production code
class SecurityCatalogService:
    def __init__(self, symbols_container):
        self.container = symbols_container

    def create_security(self, ticker, mic, display_name, ...) -> SecurityMaster
    def get_security(self, security_id) -> SecurityMaster
    def get_by_ticker(self, ticker) -> list[SecurityMaster]  # may return multiple
    def add_alias(self, security_id, raw, normalized, source, ...) -> None
    def void_alias(self, security_id, normalized, reason) -> None
    def resolve_alias(self, normalized_name) -> list[SecurityMaster]
    def update_status(self, security_id, new_status, ...) -> None
    # ... etc
```

---

## R10. Inline Creation — Container Write Sequence Update

Livingston's 4-step idempotent write protocol (§10.3) is adopted with one adjustment: the target container for Step 1 and Step 2 is `symbols` (same as the rest of the security catalog), and the `session_id`-partitioned write in Step 3 targets `import_sessions`:

| Step | Container | Partition | Document |
|------|-----------|-----------|----------|
| 0 (intent) | `import_sessions` | `{session_id}` | `pending_creation` on `import_question` doc |
| 1 (create) | `symbols` | `{ticker}` | `security_master` document |
| 2 (alias) | `symbols` | `{ticker}` | Patch `aliases[]` on `security_master` |
| 3 (session) | `import_sessions` | `{session_id}` | Update `entity_group.resolved_security_id` |
| 4 (answer) | `import_sessions` | `{session_id}` | Set `import_question.status = ANSWERED` |

Steps 1–2 are a single-partition write to `symbols` (same partition). Steps 3–4 are a single-partition write to `import_sessions` (same partition). The two partitions are in different containers → not atomically transactable → crash recovery via `pending_creation.last_step_completed` (unchanged from Livingston's design).

---

## R11. Consolidated Container Summary

```
┌─────────────────────────────────────────────────────────────┐
│  symbols (pk /symbol) — EXISTING, GAINS security_master     │
│  ┌──────────────────────────────────────────────────────── │
│  │ Partition "AAPL"                                        │
│  │   config_AAPL    (symbol_config — existing)             │
│  │   sec_XNYS_AAPL  (security_master — NEW)               │
│  │   act_AAPL_...   (activity — existing)                  │
│  │                                                         │
│  │ Partition "SAN"                                          │
│  │   config_SAN     (symbol_config — existing, US)         │
│  │   sec_XMAD_SAN   (security_master — Santander)          │
│  │   sec_XPAR_SAN   (security_master — Sanofi)             │
│  │                                                         │
│  │ Partition "ULVR"  (NEW — portfolio-only)                 │
│  │   sec_XLON_ULVR  (security_master only, no config)      │
│  └──────────────────────────────────────────────────────── │
│                                                             │
│  portfolio (pk /account_id) — NEW                           │
│  ┌──────────────────────────────────────────────────────── │
│  │ Partition "fidelity_usd"                                │
│  │   account doc, ledger_txn, ca_event, ca_leg             │
│  │                                                         │
│  │ Partition "_unassigned"                                  │
│  │   ledger_txn (no account), staged_import_row (90d TTL)  │
│  │                                                         │
│  │ Partition "_global"                                      │
│  │   import_batch (post-commit summaries)                  │
│  └──────────────────────────────────────────────────────── │
│                                                             │
│  import_sessions (pk /session_id) — NEW                     │
│  ┌──────────────────────────────────────────────────────── │
│  │ Partition "impsess_{ulid}"                               │
│  │   import_session (7-day TTL)                            │
│  │   import_question (7-day TTL)                           │
│  │   llm_call_record (7-day TTL)                           │
│  └──────────────────────────────────────────────────────── │
└─────────────────────────────────────────────────────────────┘
```

---

## R12. Impact on Prior Design Documents — Reading Guide

When reading any prior design document, apply these authoritative overrides:

| When you see... | Read it as... |
|-----------------|---------------|
| `security_id = "AAPL:XNAS"` (TICKER:MIC) | `security_id = "XNAS:AAPL"` (MIC:TICKER) |
| `portfolio._global` for security docs | `symbols.{ticker}` for `security_master` docs |
| `portfolio_ledger` (container name) | `portfolio` |
| `doc_type: "security"` | `doc_type: "security_master"` |
| `import_session` in `portfolio._global` | `import_session` in `import_sessions` container |
| `id: "sec_AAPL:XNAS"` | `id: "sec_XNAS_AAPL"` |
| `account_id: "_global"` on security doc | `symbol: "{ticker}"` (partition key is ticker) |

All other design decisions (FX convention, WHT semantics, ca_event model, question queue, warning taxonomy, commit protocol, etc.) remain unchanged and authoritative from their source documents.

---

## R13. Open Questions

| # | Question | Recommended default | Owner |
|---|----------|-------------------|-------|
| R-Q1 | Alias lookup strategy: Cosmos cross-partition query vs. in-memory index? For <200 securities, in-memory is faster and avoids cross-partition RU cost. For >500, Cosmos index is more reliable. | In-memory for Phase 1/1b; re-evaluate if catalog grows past 500. | Livingston |
| R-Q2 | `portfolio` container TTL enablement for `staged_import_row` 90-day TTL: confirm this does not interfere with permanent ledger documents that lack a `ttl` field (missing `ttl` + `defaultTtl=-1` = no expiry). | Confirmed safe by Cosmos design. Documents without `ttl` field are never expired. | Livingston |
| R-Q3 | Livingston proposed separate `llm_call_record` documents in `import_sessions`. Danny's design embedded LLM call provenance in conversation turns. Adopt Livingston's separate docs (better for audit queries). | Adopt Livingston's separate `llm_call_record` docs. | Agreed |
