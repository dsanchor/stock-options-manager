# Gap Flag: Batch Reassignment Preview Endpoint Missing

**Date:** 2026-09-06  
**From:** Rusty (Frontend Agent)  
**To:** Danny (Lead) / Livingston (Backend)  
**Status:** ✅ **RESOLVED** — Livingston added `POST /api/portfolio/movements/batch-reassign/preview`  
**Resolution date:** 2026-09-06

---

## Resolution

Livingston published the preview endpoint in `livingston-phase2-api-contract.md`:

- **Path:** `POST /api/portfolio/movements/batch-reassign/preview`  
- **Body:** same shape as batch-reassign (minus `reason`)  
- **Response:** `{ affected_count, movement_ids, sample[0..10], source_account_id, dest_account_id }`

Frontend updated:
- `types/portfolio.ts`: added `BatchReassignmentPreviewRequest`, `BatchReassignmentPreviewItem`, `BatchReassignmentPreviewResponse`
- `lib/portfolio-api.ts`: added `getBatchReassignmentPreview()`
- `components/ReassignmentDialog.tsx`: replaced warning-only fallback with full 2-step flow — (1) Preview button → shows count + sample table + zero-match empty state; (2) confirmation checkbox → enables Apply button. Re-preview available after applying filters changes. Confirmation includes exact count from preview result.

Build: ✅ exit 0, TypeScript clean.

---

~~Original gap description below (historical):~~

~~Accepted UX (item 7): "Include a preview/confirmation of affected count; no silent bulk action." Livingston's Phase 2 implementation had only POST /api/portfolio/movements/batch-reassign — no preview endpoint.~~
