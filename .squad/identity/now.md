---
updated_at: 2026-09-06T15:36:33.915Z
focus_area: Dividend Portfolio — Phase 1 MVP (awaiting user authorization)
active_issues:
  - "⚠️ Cross-partition overview latency: Monitor `GET /api/symbols/overview` as ledger grows beyond current scale (N≤3 accounts). Currently 2 Cosmos queries; consider materialization if latency exceeds 500ms."
---

# What We're Focused On

**Completed (Phase 2):** Portfolio accounts, transfers, reassignment, FX, filters — released commit `08809eb` with 478 tests passing; both API and frontend revisions deployed and healthy on 2026-09-06T11:59:49Z.

**Completed (Cost-Basis):** Portfolio CMP cost-basis implementation, Movements toolbar, rights sales, import safety guard — released commit `ff087c3` with 209 tests passing (130 cost-basis acceptance + 79 holdings/corrections); both API and frontend revisions deployed and healthy on 2026-09-06T14:08:45Z.

**Completed (Symbol Unification):** Portfolio ↔ Watchlist ↔ Symbol Details unification — unified Add Symbol UX, idempotent symbol_config enrollment (all agents/notifications disabled by default), two-section Watchlist (Portfolio symbols vs. Watchlist-only), unified Symbol Details with Portfolio holdings and movements, MIC:TICKER canonical routing with backward-compatible disambiguation — released commit `803b8f3` with 114 tests passing; both API (rev 059) and frontend (rev 052) revisions deployed and healthy on 2026-09-06T15:36:33Z. **All 12 high-risk requirements verified; zero regressions (2,952 existing tests + 114 new, 100% pass).**

**Next Priority:** Dividend Portfolio — Phase 1 MVP. User request: BUY/SELL/DIVIDEND ledger for multi-broker portfolio (Fidelity, HeyTrade, ING, Interactive Brokers), multi-currency (EUR/USD/GBP/CHF), withholding tracking (source + destination), mixed cash/share dividends. **Prerequisites met:** All three portfolio phases (Phase 2, Cost-Basis, Symbol Unification) fully stable, 687+ total tests passing, zero regressions, all phases deployed to production. Contract drafted by Danny; awaiting user confirmation on open questions.

**Secondary:** Options trading agents (covered call + cash-secured put) — deferred during portfolio MVP phase.

### Monitoring Items

1. **Cross-Partition Overview Latency** (Symbol Unification, ongoing)
   - Endpoint: `GET /api/symbols/overview`
   - Current: 2 Cosmos cross-partition queries (portfolio securities + watchlist-only)
   - Scale tested: N≤3 accounts, acceptable at current scale
   - Action: Monitor query latency as ledger grows; consider materialization if latency exceeds 500ms
   - Acceptable SLA: < 2s response time for Symbols page load

2. **Read-Repair Effectiveness** (Symbol Unification, operational)
   - Auto-enrollment during holdings compute prevents manual backfill in normal operation
   - Backfill tool available for one-time reconciliation only
   - No scheduled re-runs; read-repair ensures eventual consistency

3. **Portfolio/Watchlist Mutual Exclusivity** (Symbol Unification, verified)
   - Enforced at rendering (UI never duplicates symbol)
   - Enforced at API (counts correct, no symbol in both arrays)
   - Monitored via 18 dedicated tests; all passing
