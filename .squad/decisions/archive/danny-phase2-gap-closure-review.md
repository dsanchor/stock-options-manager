# Danny — Phase 2 Integration Gap Closure Review

**Date:** 2026-09-06
**Reviewer:** Danny (final release reviewer)
**Scope:** Two integration gaps flagged in Phase 2 revision 2 review, now closed by Livingston

## Gap A: TRANSFER_OUT / TRANSFER_IN movement filter

| Check | Status |
|-------|--------|
| `_ALLOWED_TXN_TYPES` updated in `portfolio_routes.py` | ✅ |
| Cosmos query filters correctly on `txn_type` param | ✅ |
| Frontend dropdown sends exact enum values | ✅ |
| `TxnType` union + `TXN_BADGE` extended | ✅ |
| Tests cover both directions + rejection of bare `TRANSFER` | ✅ |

## Gap B: PUT /api/portfolio/accounts/{account_id}

| Check | Status |
|-------|--------|
| Route registered, no path conflict with GET/DELETE | ✅ |
| Body whitelist: broker, name, currency, description only | ✅ |
| `id` / `account_id` from body **ignored** — immutable by construction | ✅ |
| Validation: invalid broker 400, blank name 400, empty body 400 | ✅ |
| Service preserves `created_at`, `id`, `account_id`, `doc_type`; sets `updated_at` | ✅ |
| 404 for missing and soft-deleted accounts | ✅ |
| Frontend shapes match backend (PUT with `encodeURIComponent`) | ✅ |
| Test `test_update_preserves_account_id_immutable` verifies injection rejected | ✅ |

## Route conflict analysis

- Accounts: static paths before parameterized — no ambiguity.
- Movements: `batch-reassign` / `batch-reassign/preview` use POST; no competing POST pattern at same segment depth with `{movement_id}`.

## Verdict

**APPROVED** — Both integration gaps correctly closed. Zero high-confidence blockers.
