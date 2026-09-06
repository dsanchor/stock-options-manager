# Livingston — Phase 2 Implementation Decisions

**Author:** Livingston  
**Date:** 2026-09-06  
**Status:** FINAL — Phase 2 implemented

---

## Decision 1: Account ID Format

**Choice:** `acct_{broker_slug}_{name_slug}` derived via `_slugify()` (NFKD normalization → ASCII-only → lowercase → underscores).

**Rationale:** Stable, predictable, URL-safe. Both broker and name contribute, making collisions across brokers impossible. The existing `_unassigned` virtual account is preserved without a doc.

**Impact:** Frontend must use the returned `account_id` as-is; creation is idempotent on same broker+name combination (409 on duplicate).

---

## Decision 2: Correction/Audit Chain via `correction_status`

**Choice:** New field `correction_status` on every new `ledger_txn`; absent on legacy docs = treated as ACTIVE.

- New docs set `correction_status = "ACTIVE"` at creation.
- Corrections mark original `SUPERSEDED` and create replacement with `corrects_movement_id`.
- Holdings filter: `(NOT IS_DEFINED(c.correction_status) OR c.correction_status = 'ACTIVE')`.

**Rationale:** Backward-compatible (legacy docs without the field work unchanged). The two-status query filter covers both cases without a migration.

**Alternative considered:** Soft-delete original and create replacement. Rejected because it loses the bidirectional link (original→replacement, replacement→original) needed for the audit chain UI.

---

## Decision 3: Transfer Cost Basis Convention

**Choice:** Two fields on both transfer legs:
- `transfer_cost_basis_derived_eur`: always the auto-computed avg_cost × qty
- `transfer_cost_basis_eur`: effective value (equals derived unless overridden)
- `transfer_cost_basis_overridden`: boolean flag

**Rationale:** Both values must be persisted for audit purposes. The derived value documents what the system computed; the effective value is what holdings use. Override flag allows UI to clearly show "user-adjusted".

---

## Decision 4: FX Service Architecture

**Choice:** In-process cache (per-process dict), ECB public XML endpoint, adjacent-day fallback (up to 5 days).

**Rationale:** ECB API is free, well-known, matches existing `rate_source: "ECB"` convention. `requests` is already in requirements. No new package needed. Cache avoids repeated external calls during a single request burst.

**Limitation:** Cache is lost on restart. Acceptable for Phase 2 (FX rates for past dates are stable). Phase 3 may add a Cosmos-backed cache if needed.

---

## Decision 5: Reassignment Safety Protocol

**Choice:** Write-then-mark (destination first, then mark original SUPERSEDED).

1. Write new doc in dest partition (with `reassigned_from` provenance).
2. Mark original SUPERSEDED in source partition.

**Rationale:** If step 2 fails, the new doc exists with a `reassigned_from` field that allows detection on retry (the original isn't yet SUPERSEDED, so a retry would find it again and attempt to SUPERSEDE it — safe and idempotent). If we reversed the order (mark SUPERSEDED first, then write), a crash after step 1 would leave an invisible/inaccessible movement.

**Limitation:** Not a true atomic cross-partition transaction (Cosmos doesn't support this across partition keys). The protocol is safe but has a brief window where both the original and copy exist as ACTIVE. The write takes milliseconds; window is negligible for this use case.

---

## Decision 6: Route Ordering for `batch-reassign`

`POST /api/portfolio/movements/batch-reassign` is declared BEFORE `POST /api/portfolio/movements/{movement_id}/correct` and `POST /api/portfolio/movements/{movement_id}/reassign` in the router.

**Rationale:** FastAPI matches routes in declaration order. If `{movement_id}` is declared first, the literal string `"batch-reassign"` would be captured as a `movement_id` parameter. Placing the literal route first avoids this collision.

**Note to Rusty:** No frontend change needed — the endpoint path `/api/portfolio/movements/batch-reassign` is a distinct URL from `/api/portfolio/movements/{id}/reassign`.

---

## Decision 8: Movements Filter Extension — TRANSFER_OUT / TRANSFER_IN (Gap A)

**Change:** `GET /api/portfolio/movements?txn_type=` now accepts `TRANSFER_OUT` and `TRANSFER_IN` in addition to `BUY`, `SELL`, `DIVIDEND`. Bare `TRANSFER` remains invalid (400).

**Rationale:** `PortfolioMovementsTable.tsx` already offered TRANSFER_OUT/TRANSFER_IN options in the filter dropdown. The backend's original Phase 1 filter set was never updated for Phase 2 types. The expansion is a pure superset; all existing valid types still work.

---

## Decision 9: Account Update (PUT) — Immutable ID (Gap B)

**Change:** Added `PUT /api/portfolio/accounts/{account_id}` endpoint and `update_account()` service method. Accepted fields: `broker`, `name`, `currency`, `description`. `account_id` is never mutated regardless of body content.

**Rationale:** Frontend `AccountsView.tsx` already called `updateAccount()` → PUT, receiving 405. ID is kept immutable because the slug derives from broker+name at creation time; renaming would silently break foreign references in ledger_txn documents. A future safe migration would require an explicit rename endpoint with movement re-partitioning — out of scope here.


**Choice:** `TRANSFER_IN` adds `transfer_cost_basis_eur` to `total_cost_eur` and the transferred qty to `paid_buy_shares`.

**Rationale:** This preserves the average cost across accounts. After a transfer, the destination account's average cost correctly reflects the carried basis, and further BUYs in that account blend correctly.

**Note:** `TRANSFER_IN` does NOT count toward `total_purchases_eur` — it is not a purchase. The distinction matters for reporting (total purchases = money spent buying securities, not shares received via transfer).
