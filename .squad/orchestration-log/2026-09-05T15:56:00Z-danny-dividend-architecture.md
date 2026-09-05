# Danny — Dividend Portfolio Architecture Design

**Timestamp:** 2026-09-05T15:56:00Z  
**Mode:** Sync (Lead)  
**Role:** Lead, Architecture Design Lead  
**Status:** ✅ Complete

---

## Outcome

Designed the comprehensive Dividend Portfolio architecture as a **ledger-first system** independent from the existing watchlist/symbols domain. Delivered with full data model, migration strategy, and phased MVP scope.

**Key Architecture Decisions:**
- **Separation of concerns:** Portfolio movements (BUY/SELL/DIVIDEND) in new `portfolio` Cosmos container; watchlist flags remain in `symbols` container
- **Ledger-first immutability:** All movements append-only with soft-delete; corrections are new movements or reversals
- **Derived holdings:** Never stored; always computed as `SUM(buys) - SUM(sells) + SUM(dividend_shares)` per symbol/broker
- **Multi-currency with EUR base:** All amounts stored in transaction currency AND EUR equivalent; FX rates embedded with provenance
- **Broker identity as first-class:** Fidelity, HeyTrade, ING, Interactive Brokers each with distinct withholding, FX, and fee patterns
- **Phased migration of total_shares:** MVP coexists with existing `symbol_config.total_shares`; Phase 2 Excel import enables reconciliation; Phase 3 derive from ledger
- **Mixed cash/share dividends:** Atomic modeling with optional stock leg for scrip/DRIP events

---

## Design Outputs

**Primary Document:** `.squad/decisions/inbox/danny-dividend-portfolio-architecture.md` (891 lines)

**Sections:**
1. **Domain Boundaries & Navigation:** Conceptual model, menu changes, total_shares migration path
2. **Ledger-First Data Model:** Design principles, movement schema, invariants
3. **Holdings Derivation:** Read-only computation, cost-basis method (average cost MVP)
4. **Broker Profiles vs. Transaction Facts:** Configuration layer + immutable facts
5. **Multi-Currency Model:** FX direction convention, rate provenance, fee/withholding semantics
6. **Mixed Cash/Share Dividend:** Atomic model with optional share leg
7. **MVP Pages, Forms, Tables, APIs & Persistence:** Complete surface specification
8. **Coexistence with Options & Economics:** Domain isolation strategy

**Cross-references:** Aligned with Livingston's persistent design and Rusty's UX design

---

## Technical Decisions

### FX Convention (Critical)
- **Convention: `fx_rate` is `transaction_currency / EUR`** (always reciprocal of ECB rate)
- Arithmetic: `eur_amount = amount_txn × fx_rate`
- Example: USD/EUR = 0.91730 means $27,375 × 0.91730 = €25,111.29
- Rationale: Consistent, unambiguous, matches Spanish tax reporting

### Withholding Null vs. Zero Distinction
- **`withholding_destination: null`** = broker doesn't capture this; distinct from `{amount: 0}` which means confirmed zero
- Critical for fiscal export (Phase 4): identifies outstanding tax liabilities vs. paid withholding
- UI rule: null renders as ⚠️ "Pending" / "Not captured", never as €0.00

### Cost Basis Method
- **MVP default: average cost** (simplest, matches Spanish FIFO-like scenarios)
- FIFO/LIFO deferred to Phase 3
- Configurable at portfolio level; applied uniformly to all holdings

### Holdings Materialization
- **Not stored.** Computed on read: cross-partition query + in-memory aggregation
- Performance: ~500 movements (realistic for 8 years) is sub-second
- Snapshot introduction deferred to Phase 3 if ledger grows to thousands

---

## Assignments to Team

- **Livingston (Persistence):** Full persistence design for ledger model, broker profiles, FX/withholding field sets, Cosmos container/partition strategy
- **Rusty (UX/Frontend):** Navigation, page layouts, form flows, accessibility, mobile responsiveness
- **Basher (pending):** Specification validation, cross-design consistency review

---

## Phased Roadmap

**Phase 1 (MVP):** Manual entry of movements; read-only holdings; parallel total_shares coexistence  
**Phase 2:** Excel import; reconciliation tool between total_shares and ledger  
**Phase 3:** Ledger-derived total_shares; cost-basis method selection (FIFO/LIFO); snapshot optimization  
**Phase 4:** Fiscal export; tax reporting integration  
**Phase 5:** Charts, Analytics, Economics integration  

---

## Next Steps

1. Livingston: implement persistence layer design (in progress)
2. Rusty: implement UX/navigation design (in progress)
3. Scribe: merge all three designs into `.squad/decisions.md`
4. User: review consolidated recommendation; confirm open questions or proceed to implementation

---

**Lead:** Danny (Architecture)  
**Date:** 2026-09-05T15:56:00Z  
**Verdict:** ✅ **DESIGN COMPLETE — READY FOR TEAM REVIEW & CONSOLIDATION**
