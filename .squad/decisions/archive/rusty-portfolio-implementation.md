# Rusty Frontend Portfolio Implementation — Decision Record

**Date:** 2026-09-05  
**Author:** Rusty (Frontend Agent)  
**Status:** RECORDED  
**Ref:** `danny-portfolio-implementation-contract.md` v1.1

---

## Decisions Made During Implementation

### D1 — `[[...slug]]` optional catch-all for proxy routes

**Contract gap:** The contract specifies three proxy route files but does not specify how to handle both the base path (e.g., `GET /api/securities`) and sub-paths (e.g., `GET /api/securities/XNYS:AAPL`) from a single route file.

**Decision:** Used Next.js `[[...slug]]` optional catch-all in all three proxy routes. This handles both the base path (no slug) and sub-paths (slug present) from a single `route.ts`, matching how `req.nextUrl.pathname` naturally reconstructs the full target URL.

**Alternative considered:** Two separate files per endpoint group (one for base, one for sub-paths). Rejected as more verbose and harder to maintain.

---

### D2 — Multipart proxy via `arrayBuffer()` + verbatim Content-Type

**Contract gap:** The contract specifies `POST /api/import/sessions` uses `multipart/form-data` but does not specify how the Next.js proxy should forward the file body.

**Decision:** The proxy reads the raw body with `req.arrayBuffer()` and forwards the original `content-type` header verbatim (including the `multipart/form-data; boundary=XXX` boundary). This preserves the multipart encoding end-to-end.

**Alternative considered:** `req.blob()` — equivalent, `arrayBuffer()` chosen for explicit typing.

---

### D3 — Answer flow for inline security creation

**Contract gap:** The `CREATED_NEW_SECURITY` answer type is defined but the contract does not specify whether the client creates the security first via `POST /api/securities` and then answers, or passes the security data inline in the answer body.

**Decision:** `SecurityCreateForm` calls `POST /api/securities` first. On success, the parent `ImportQuestionCard` submits an answer with `answer_type: "CREATED_NEW_SECURITY"` and `selected_security_id: <new_id>`. This keeps security creation atomic and auditable separately from the question answer.

---

### D4 — Securities catalog page deferred

**Contract observation:** The contract says the Securities catalog "evolves the existing Symbols area or occupies a separate route — it is NOT under the Portfolio nav." The owned-files list for Rusty does not include a `/securities/page.tsx`.

**Decision:** No standalone Securities catalog page implemented in Phase 1. Securities are accessible via:
- Inline creation during import (SecurityCreateForm)
- `GET /api/securities` via the proxy (programmatic access)

A standalone catalog page can be added in Phase 1.5 without architectural changes.

---

### D5 — `react-hooks/set-state-in-effect` lint suppression

**Observation:** The `useEffect(() => { load(); }, [load])` pattern in `PortfolioHoldingsTable.tsx` and `PortfolioMovementsTable.tsx` triggers the `react-hooks/set-state-in-effect` ESLint rule. The same pattern exists in at least 4 pre-existing files (`AgentMarkdownView.tsx`, `BestOptionsView.tsx`, `OptionsScreenerView.tsx`, `options-chain/page.tsx`) without suppression.

**Decision:** Added `// eslint-disable-next-line react-hooks/set-state-in-effect` on the affected lines. Consistent with the pre-existing pattern in the codebase. Not a runtime risk — the async function is called once at mount.

---

### D6 — `_unassigned` account display

**Contract direction (v1.1):** "broker/account may be blank without a warning." The stored value is `_unassigned`.

**Decision:** Account column in Holdings and Movements tables displays `_unassigned` as `—` (em dash). No warning icon, no tooltip. This is neutral and informative without being alarmist.
