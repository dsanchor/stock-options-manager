---
updated_at: 2026-09-06T14:08:45.567Z
focus_area: Symbol Details ↔ Portfolio movements ↔ symbol_config unification (PAUSED)
active_issues: []
---

# What We're Focused On

**Completed (Phase 2):** Portfolio accounts, transfers, reassignment, FX, filters — released commit `08809eb` with 478 tests passing; both API and frontend revisions deployed and healthy on 2026-09-06T11:59:49Z.

**Completed (Cost-Basis):** Portfolio CMP cost-basis implementation, Movements toolbar, rights sales, import safety guard — released commit `ff087c3` with 209 tests passing (130 cost-basis acceptance + 79 holdings/corrections); both API and frontend revisions deployed and healthy on 2026-09-06T14:08:45Z.

**Next Priority (DEFERRED):** Symbol Details ↔ Portfolio movements ↔ symbol_config unification — consolidate three currently separate symbol management areas. Enable Watchlist-only symbols (no portfolio holdings required); auto-add Portfolio symbols to Watchlist with agents/notifications disabled.

**Prerequisites met:** Both Portfolio Phase 2 and Cost-Basis phases fully stable, 687 total tests passing (478 + 209), zero regressions, all phases deployed to production.

**Secondary:** Options trading agents (covered call + cash-secured put) — deferred during portfolio MVP phase.
