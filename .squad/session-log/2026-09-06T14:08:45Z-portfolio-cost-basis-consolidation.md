---
session_id: 982fe3ee-631f-4684-a682-b5dc4ee47185
timestamp: 2026-09-06T14:08:45.567Z
scribe: Copilot
phase: Portfolio Cost-Basis Consolidation
---

# Session Log: Portfolio CMP Cost-Basis Implementation & Final Approval

## Executive Summary

**Portfolio Cost-Basis Phase** (moving weighted average cost calculation, Movements toolbar design, rights sales, import safety) reached full implementation, testing, and production approval on 2026-09-06. Four agents (Danny, Linus, Basher, Rusty) collaborated across architecture, implementation, testing, and design validation. Final review approved all 14 acceptance criteria; commit `ff087c3 fix: report remaining portfolio cost basis` passed 209 tests; API and frontend deployed and healthy on sha-ff087c3.

---

## Outcome Context

### Semantic Correction (User Request)
**User directive (2026-09-06 morning):** Current `current_invested_eur` incorrectly mixes base cost with sale proceeds. Must subtract **acquisition cost of sold shares**, not sale proceeds. Requested method: FIFO (First In, First Out).

**Team Decision:** CMP (Coste Medio Ponderado / Moving Weighted Average) adopted after review. Advantages: chronological determinism, transparent cost pool, avoids FIFO's anti-lavado complexity. Explicitly non-fiscal, documented in UI tooltips.

### Key Implementations
1. **Backend Cost-Basis Algorithm (Danny/Linus):**
   - Per-security CMP pool: `pool_shares`, `pool_cost`, `avg_cost = pool_cost / pool_shares`
   - Chronological ordering: `trade_date ASC`, then `id ASC` (deterministic tie-break)
   - BUY: cost includes commission (gross + fee)
   - SELL ACCIONES: deducts cost at current CMP; decrements share pool
   - SELL DERECHOS: adds to proceeds only; leaves share pool untouched
   - TRANSFER: preserves global cost basis across accounts
   - INCOMPLETE/zero-cost: flagged with warnings; cost 0 assigned to sales
   - Corrections/deleted/superseded: excluded from computation
   - Backward-compatible aliases: `total_purchases_eur`, `total_sales_eur`, `total_invested_eur` (unchanged numerically)

2. **New Summary Fields (Danny design, Linus/Rusty implementation):**
   - `remaining_cost_basis_eur` (replaces `current_invested_eur` semantically)
   - `cost_basis_sold_eur` (CMP basis assigned to shares sold)
   - `total_purchase_outflow_eur` (gross + commission of all BUY COMPLETE)
   - `total_sale_proceeds_eur` (gross − commission of all SELL, both types)
   - `rights_proceeds_eur` (desglose informativo)
   - `realized_result_eur` (sale proceeds − cost basis sold)
   - `has_incomplete_cost_basis` (global warning flag)

3. **Frontend UI (Rusty design, refined with Danny gate):**
   - Primary row: "Inversión actual" · "Resultado realizado" · "Dividendos netos"
   - Secondary row (desglose): "Total compras" · "Coste vendido" · "Ingresos ventas"
   - StatCard + Reveal pattern matches Economics/Dashboard KPI hierarchy
   - Tooltips explicitly state "Calculado con media ponderada móvil (CMP). No es FIFO ni válido para fines fiscales."
   - Movements filter card with Refresh/Add/Bulk actions outside

4. **Safety Guard (Danny/Linus):**
   - `write_ledger_txn` checks for restoration of VOIDED/SUPERSEDED movements during import
   - Prevents silent data corruption on re-import of historical CSV
   - 5 dedicated tests confirm correctness

5. **Movements Toolbar (Rusty/Danny refinement):**
   - Filter controls in card, action buttons (Refresh/Add/Bulk) in separate row above
   - Refresh respects current filters + pagination
   - Accessible aria-labels for all controls

### Test Results
- **Targeted cost-basis acceptance:** 130 tests (Basher suite) — PASS
- **Holdings + corrections + summary:** 79 tests — PASS
- **Total portfolio suite:** 209 tests — PASS
- **TypeScript:** `tsc --noEmit` clean
- **No regressions:** All pre-existing options tests green

### Approval Gates
1. **Linus implementation review (Danny):** ✅ APPROVED
2. **Cost-basis algorithm review (Danny):** 14 requirements verified — ✅ APPROVED
3. **UI pattern gate (Rusty StatCard matching):** ✅ APPROVED
4. **Voided-movement import guard:** ✅ APPROVED (included in release)
5. **Final integration review (Danny):** Zero high-confidence blockers — ✅ APPROVED

---

## Consolidated Decisions

The following decisions were archived from inbox:
1. `danny-portfolio-summary-cost-basis.md` — CMP architecture, field definitions, algorithm, tests
2. `danny-review-portfolio-cmp-cost-basis.md` — Final approval gate, 14 requirements satisfied

**Merge Strategy:** Both files consolidated into canonical `.squad/decisions.md` §4 "Portfolio Cost-Basis Implementation (2026-09-06)". Proposal→Approval history preserved.

---

## Deployed State

**Functional Commit:** `ff087c3 fix: report remaining portfolio cost basis`
- **API Revision:** ca-stock-options-manager-api--0000054
- **Frontend Revision:** ca-stock-options-manager-front--0000047
- **GitHub Actions Run:** 34037938698
- **Status:** ✅ PASSED — API and frontend deployed and healthy

**Summary Fields in Production:**
- `remaining_cost_basis_eur` ← replaces old `current_invested_eur` semantics
- `cost_basis_sold_eur`, `total_purchase_outflow_eur`, `total_sale_proceeds_eur`, `realized_result_eur`
- `has_incomplete_cost_basis` global flag
- Old aliases unchanged: `total_purchases_eur`, `total_sales_eur`, `total_invested_eur`

---

## Next Phase

**User Directive (Deferred):** Symbol Details ↔ Portfolio movements ↔ symbol_config unification
- Enable Watchlist-only symbols (no portfolio holdings required)
- Auto-add Portfolio symbols to Watchlist with agents/notifications disabled
- Consolidate three currently separate symbol management areas

**Active Status:** Portfolio cost-basis fully stable and released. Symbol unification paused/deferred.

---

## Cross-Agent Contributions

### Danny (Lead Architect / Reviewer)
- Proposed CMP cost-basis algorithm (override FIFO request)
- Designed all new summary fields and their formulas
- Identified and corrected realized_result double-count bug in draft contract
- Performed final 14-requirement gate review
- Approved voided-movement import guard inclusion
- **Status:** All gates passed; ready for release

### Linus (Implementation)
- Implemented CMP algorithm with chronological determinism
- Added 130 acceptance test scenarios (S1–S16 detailed in danny-cost-basis contract)
- Implemented write_ledger_txn safety guard (5 tests)
- **Status:** All tests passing, ready for deployment

### Basher (QA / Testing)
- Authored 130-test cost-basis acceptance suite
- Verified all 14 Danny review requirements via test scenarios
- Validated holdings consistency, transfer preservation, incomplete handling
- **Status:** Zero regressions, production-ready

### Rusty (Frontend)
- Implemented StatCard + Reveal summary UI hierarchy
- Designed Movements filter card with action buttons
- Added CMP-specific tooltips
- Verified `tsc --noEmit` clean
- **Status:** UI pattern gate passed

---

## Documentation Artifacts

- **Session Log:** This file (2026-09-06T14:08:45Z)
- **Orchestration Log:** `.squad/orchestration-log/2026-09-06T14:08:45Z-portfolio-cost-basis-consolidation.md`
- **Identity Update:** `.squad/identity/now.md` — Portfolio Phase 2 + Cost-Basis ✅ COMPLETE
- **Decision Merge:** Canonical `.squad/decisions.md` — inbox files merged with approval history preserved
- **Inbox Archive:** `danny-portfolio-summary-cost-basis.md`, `danny-review-portfolio-cmp-cost-basis.md` moved to archive

---

## Commit Message

```
docs: consolidate Portfolio cost-basis implementation and final approvals

Consolidate Danny's CMP cost-basis contract and final review into canonical
decisions.md. Portfolio Phase 2 (accounts/transfers/FX) + Cost-Basis (CMP
algorithm, new summary fields, Movements toolbar, rights sales, import
guard) fully implemented, tested, and deployed on sha-ff087c3.

All 14 review requirements satisfied:
- CMP chronological & deterministic
- BUY includes commission; SELL subtracts
- SELL ACCIONES removes CMP basis; SELL DERECHOS untouched
- Transfers preserve global cost
- Incomplete/zero-cost handled safely
- Negative inventory ≥ 0 remaining
- Full exit clears pool, avg=null
- Corrections/deleted/superseded excluded
- API backward-compatible aliases
- Summary portfolio-wide, filter-independent
- No tax/FIFO claims in UI
- Movements toolbar/filters correct
- Voided import guard included
- UI StatCard pattern matches Economics/Dashboard

Tests: 209/209 pass (130 cost-basis + 79 holdings+corrections)
TypeScript: tsc --noEmit clean
Deployed: sha-ff087c3, both API and frontend healthy
```

---

## Session Context

**Team:** Danny (lead), Linus (implementation), Basher (QA), Rusty (frontend)
**Duration:** 2026-09-06 morning → 2026-09-06 afternoon
**Outcome:** APPROVED for production deployment
**Next Phase:** Symbol Details ↔ Portfolio unification (deferred)
