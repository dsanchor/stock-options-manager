# Livingston — Dividend Portfolio Persistence & Domain Model Design

**Timestamp:** 2026-09-05T15:56:01Z  
**Mode:** Sync (Persistence & Integration)  
**Role:** Persistence Engineer, Data Model Design  
**Status:** ✅ Complete

---

## Outcome

Designed the complete **persistence layer for dividend portfolio ledger** including transaction document schema, security identity model, broker account profiles, FX conventions, withholding semantics, and Cosmos DB container strategy.

**Key Persistence Decisions:**
- **Container strategy:** New `portfolio_ledger` container (partition key: `/account_id`) for all transactions; broker profiles in existing `settings` container
- **Security identity:** ISIN as primary canonical identifier; embedded `security` object in every transaction (denormalized for performance)
- **Transaction document:** Comprehensive schema with `account_id`, `txn_type`, security, dates, quantities, prices, FX, withholding, fees, audit fields
- **FX precision:** Decimal strings (9 dp for rates, 6 dp for amounts) for arithmetic fidelity and future tax reporting
- **Withholding model:** Dual-layer (source country + destination country); null vs. zero distinction preserved
- **Broker profiles:** Four profiles (Fidelity, HeyTrade, ING, Interactive Brokers) as behavior hints, not stored constraints
- **Audit trail:** Immutable with revision chain, soft-delete flags, dedup keys for import idempotency
- **Cost-basis derivation:** Average cost for MVP; FIFO/LIFO reserved for Phase 3

---

## Design Outputs

**Primary Document:** `.squad/decisions/inbox/livingston-dividend-ledger-model.md` (878 lines)

**Sections:**
1. **Scope & Non-Goals:** MVP boundaries, out-of-scope phases, design constraints
2. **Domain Overview:** Entity relationships (BrokerAccount, LedgerTransaction, Security, FxRate, WithholdingRecord, Holdings)
3. **Security Identity Model:** ISIN as canonical, secondary identifiers (CUSIP, SEDOL, ticker), broker IDs, corporate actions
4. **Broker Account Identity Model:** Four profiles with regulatory/fee/withholding behaviors
5. **Transaction Document Model:** Core schema with all field sets (quantity, price, gross/net, FX, withholding, audit)
6. **Data Type Specifications:** Decimal precision, string enums, date formats, validation rules
7. **Holdings Derivation:** Read-only computation with cost-basis methods
8. **Edit/Void Strategy:** Soft-delete, correction chains, dedup keys for import
9. **FX & Withholding Deep Dive:** Rate conventions, ECB reference, trader rules, treaty handling
10. **Cosmos DB Provisioning (Future):** RU budgets, indexing, backup policies

**Cross-references:** Aligned with Danny's architecture and Rusty's UX flows

---

## Technical Specifications

### Transaction Document ID Construction
- **Format:** `txn_{account_id}_{timestamp|dedup_hash}_{seq:05d}`
- **Example:** `txn_heytrade_main_20240115_AAPL_BUY_00001`
- **Purpose:** Stable, sortable, human-readable; supports import dedup

### Decimal Precision Rules
- **Amounts (gross/net/fees/withholding):** 6 decimal places (0.000001 EUR)
- **Quantities (shares):** 6 decimal places (fractional share support)
- **FX rates:** 9 decimal places (0.000000001 for precision in rate composition)
- **Rationale:** Matches broker statement precision; supports future tax rounding rules

### FX Rate Convention
- **Stored as:** `rate = transaction_currency_per_eur`
- **Formula:** `eur_amount = amount_txn × rate`
- **Example:** USD/EUR rate = 0.91730 (how many USD per 1 EUR)
- **Rate sources:** ECB (preferred), BROKER (HeyTrade/ING), MANUAL (user), OVERRIDE (user change)
- **Auditability:** Original rate preserved when overridden

### Withholding Two-Layer Model
| Field | Source | Destination | BUY | SELL | DIV |
|-------|--------|-------------|-----|------|-----|
| `withholding.source_*` | Tax withheld in security's country (e.g., US 15%) | Investor's country for credit | — | — | ✓ |
| `withholding.destination_*` | (N/A, not applicable) | IRPF or local tax | — | — | ✓ |
| **null vs. zero** | `null` = broker doesn't capture; `{amount: 0}` = confirmed zero | Critical for fiscal export |

### Broker Profile Hierarchy (Non-Stored)
**Fidelity:** USD-native, CUSIP-primary, 15% US withholding, no destination capture  
**HeyTrade:** EUR-native, ISIN-primary, auto-converts USD → EUR, both layers visible  
**ING:** EUR-native, ISIN-primary, commission-bearing, captures both withholding layers  
**Interactive Brokers:** Multi-currency, IBKR conid + ISIN, complex W-8BEN scenarios, origin only  

Each profile has defined defaults for forms (currency, FX handling, withholding visibility) but does **not constrain** transaction facts.

---

## Validation Rules (Invariants)

| # | Invariant | Enforced Where |
|---|-----------|-----------------|
| I1 | `txn_type ∈ {BUY, SELL, DIVIDEND, SCRIP_CASH_LEG, SCRIP_SHARE_LEG}` | API input validation |
| I2 | `quantity > 0` for all txn_types; direction in txn_type | API |
| I3 | Every money field has `{amount, currency}`; `eur_amount`/`fx_rate` required when `currency ≠ EUR` | API |
| I4 | `withholding_destination = null` ≠ `{amount: 0}`; UI treats null as "not captured" | UI rendering + API |
| I5 | Derived holdings never negative for `(account_id, isin)` at any point in ledger | API (chronological validation) |
| I6 | Movements append-only; corrections are new documents or soft-delete | API design |
| I7 | `net = gross - fees - withholding_source - withholding_destination` (EUR conversion) | API (computed field) |
| I8 | `deleted_at` (soft-deleted) excluded from all aggregates and holdings | Query filter |

---

## Import Deduplication (Phase 2 Readiness)

**Dedup key format:** `{account_id}|{isin}|{txn_type}|{trade_date}|{gross_txn_2dp}|{txn_currency}`

**Example:** `heytrade_main|US0378331005|BUY|2024-01-15|27375.00|USD`

**Purpose:** Idempotent re-import from Excel or broker API without creating duplicates

**Collision handling:** If a dedup key already exists, the import either:
1. Skips (existing is authoritative)
2. Compares checksums of key fields (quantity, price, withholding rates) and flags inconsistencies
3. Allows manual override with warning

---

## Assignments to Team

- **Danny (Architecture):** Validated integration with top-level domain boundaries and navigation
- **Rusty (UX):** Form/field validation requirements, field visibility rules, FX/withholding UI mapping
- **Basher (pending):** Specification cross-check, schema consistency, data integrity edge cases

---

## Deferred to Phase 2+

- **Excel import implementation** (parser, batch processing, conflict resolution UI)
- **FX rate auto-fetch** (ECB API integration, caching, fallback rules)
- **Corporate action events** (ticker changes, splits, mergers, retroactive adjustments)
- **Tax settlement tracking** (fiscal year binding, declaration references, audit export)
- **Cosmos DB provisioning** (RU budgets, indexing policies, backup/restore)

---

## Next Steps

1. Rusty: map UX form fields to this schema; confirm accessibility for all numeric/FX inputs
2. Danny: validate cross-domain references (symbol → security link, total_shares → holdings)
3. Scribe: merge into `.squad/decisions.md`
4. Implementation team (future): begin backend API design and Cosmos DB provisioning

---

**Persistence Engineer:** Livingston  
**Date:** 2026-09-05T15:56:01Z  
**Verdict:** ✅ **PERSISTENCE DESIGN COMPLETE — READY FOR INTEGRATION**
