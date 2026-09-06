### 2026-09-06T11:33:35+02:00: User directive
**By:** Copilot user (via Copilot)
**What:** In unresolved-company import questions, add a "Find in portfolio" option that lets the user search and select an existing security when the CSV company name differs because of spacing or a typo.
**Why:** User request — captured for team memory

### 2026-09-06T11:33:58+02:00: Implementation — Rusty
**By:** Rusty (Copilot sub-agent)
**Files changed:**
- `frontend/src/lib/filterSecurities.ts` *(new)* — pure, exported `filterSecurities(securities, query)` helper; case-insensitive match on `security_id`, `ticker`, `company_name`, and `aliases[].value`. No framework dependency; testable standalone.
- `frontend/src/components/SecuritySearchPanel.tsx` *(new)* — "Find in portfolio" panel; lazy-loads `listSecurities()` on first mount, accepts `cachedSecurities`/`onCacheLoaded` props to avoid duplicate requests across panel re-opens. Shows loading / no-results / error states. Results capped at 50 with a "refine search" hint. Accessible: labelled `<input id>` with `sr-only` label, `role="listbox/option"`, `aria-label` on close button. Calls parent `onSelect(security_id)` which maps to existing `handleSelect` → `SELECTED_CANDIDATE`.
- `frontend/src/components/ImportQuestionCard.tsx` *(modified)* — Added `showSearch` state and `cachedSecurities` state. "Find in portfolio" and "+ Create new security" buttons are mutually exclusive (opening one hides the other). Skip/Exclude always visible. `SecuritySearchPanel` is prefilled with `question.company_name`.
**Validation:** `tsc --noEmit` → 0 errors; `eslint` on changed files → 0 warnings/errors.
**No backend changes.** Selection reuses existing `SELECTED_CANDIDATE` answer type and backend fan-out logic.
