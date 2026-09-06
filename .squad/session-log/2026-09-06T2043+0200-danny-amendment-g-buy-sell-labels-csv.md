# Danny — Session Log: Amendment G — BUY/SELL Clarity, English Labels & Bilingual CSV

**Date:** 2026-09-06  
**Role:** Lead Architect  
**Task:** Refine active correction contract with new requirements for manual BUY/SELL UX, English sale-type labels, and bilingual CSV import  
**Status:** COMPLETE  
**Output:** Amendment G appended to `.squad/decisions/inbox/danny-zero-filter-full-correction-contract.md`

---

## Context

New user requirements arrived concerning:
1. Manual BUY creation must show `quantity × price = trade value`, then `trade value + fees = total cost`
2. Equivalent for SELL with net proceeds after fees
3. Labels "without fees" / "with fees" instead of ambiguous gross/net
4. Sale type labels in English (Stocks/Rights) instead of ACCIONES/DERECHOS
5. CSV import must accept both Spanish and English headers and type values

## Approach

1. Read existing contract (Parts A–F, 478 lines)
2. Read backend models (`models.py`), `cosmos_portfolio.py` (create_manual_movement, correct_movement), all three parsers (purchases, sales, dividends), and frontend (`AddMovementDialog.tsx`, types)
3. Mapped gross/net backend semantics: `gross` = trade value (qty×price), `net = gross − fees − wht`
4. Confirmed no backend formula changes needed — `gross` is already "trade value before fees"
5. Designed source-of-truth policy: frontend cross-validates qty×price vs gross within 0.01 tolerance; server only sees gross
6. Designed bilingual header alias maps for all three parsers
7. Designed type alias extension: Stocks/Shares→ACCIONES, Rights→DERECHOS
8. Kept internal ACCIONES/DERECHOS enum to avoid Cosmos migration risk

## Key Decisions

| # | Decision | Rationale |
|---|---|---|
| 1 | Keep `ACCIONES`/`DERECHOS` as internal canonical enum | Cosmos migration risk; 100+ existing documents |
| 2 | `SALES_TYPE_LABELS` frontend constant for display mapping | Single source of truth for label translation |
| 3 | Unit price is UI-only; server never sees it | Deterministic: server owns `net = gross − fees − wht` |
| 4 | Cross-validation tolerance = 0.01 in transaction currency | Covers broker rounding; strict enough to catch real errors |
| 5 | Non-empty unrecognized CSV type value → hard error | Prevents silent data corruption; empty defaults preserved for legacy |
| 6 | Alias-based header matching (superset of exact match) | Full backward compatibility; English headers additive |

## Files Modified

- `.squad/decisions/inbox/danny-zero-filter-full-correction-contract.md` — Amendment G (sections G.1–G.6) appended, Amendment H (sections H.1–H.6) appended

## Agent Impact

- **Livingston:** 3 parser files + parser tests (G); models + cosmos_portfolio + routes for corporate-action groups (H)
- **Rusty:** 7 frontend files for labels/computations (G); multi-leg wizard + WHT derivation + group display (H)
- **Basher:** E2E tests for bilingual import (G); corporate-action group lifecycle tests (H)

---

## Amendment H — Additional Context (2026-09-06T20:47)

**New user requirements:** WHT percentages derived from amounts, composite corporate-action events (cash+scrip+rights+topup) as linked legs.

**Key architectural decision:** The `ca_event`/`ca_leg` model from `decisions.md §2.1` is NOT yet implemented in production (only `ledger_txn` exists). Full implementation is 3–4 sprints. Solution: Phase H-α (IN SCOPE) creates linked `ledger_txn` documents with `ca_group_id`, reusing existing infrastructure. Phase H-β (DEFERRED) migrates to proper `ca_event`/`ca_leg` model later.

**Holdings compatibility:** Each leg maps to standard `txn_type` values and is processed by existing `holdings_service.py` without modification:
- CASH_DIVIDEND → DIVIDEND (no share change)
- RIGHTS_SOLD → SELL + DERECHOS (no share change — existing pathway)
- SHARE_ACQUISITION → BUY (adds shares; cost basis per COMPLETE/INCOMPLETE)
- CASH_TOP_UP → BUY + qty=0 + INCOMPLETE (no shares, no pool cost — records outflow only)

**22 new acceptance criteria (H-1 through H-22) added.**

---

## Amendment I — Symbol Detail Options/Stocks Reorganization (2026-09-06T20:52)

**New user requirement:** Symbol Detail must organize into "Options" and "Stocks" sections.

**Key decisions:**
1. **Stacked sections, not tabs** — existing page uses stacked cards; tabs would break deep-link conventions and hide one domain. User often owns both options and stocks for same symbol.
2. **Options section:** SymbolSummary + PositionsTable + AddPositionForm + RecentActivities
3. **Stocks section:** PortfolioHoldingsCard + new `StockTransactionsTable` (full BUY/SELL/DIVIDEND history)
4. **StockTransactionsTable replaces SymbolMovementsTable** — enhanced with 7 columns (date, type+subtype, qty, gross, fees, wht, net), pagination (20/page), type-filter pills, row-click to MovementDetailDialog. Auto-hides fees/WHT columns when all zero.
5. **No backend changes** — StockTransactionsTable calls existing `GET /api/portfolio/movements?security_id=...` endpoint returning full `LedgerMovement` objects.
6. **Reusable `DetailSection` component** — collapsible section container, both sections default expanded.

**19 acceptance criteria (I-1 through I-19) added.**
