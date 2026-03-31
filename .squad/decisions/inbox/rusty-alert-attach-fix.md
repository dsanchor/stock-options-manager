# Decision: Alert checkbox attaches source metadata, does not pre-fill form

**Date:** 2026-07
**Author:** Rusty
**Status:** Implemented

## Context
The alert checkbox on the Add Position form (symbol detail page) was pre-filling strike/expiration/notes from alert data. User wanted it to transparently attach alert source metadata instead, with no form field changes.

## Decision
- Checkbox sends `source_activity_id` in POST body to `/api/symbols/{symbol}/positions`
- Backend looks up the activity and builds the same `source` dict used by the from-activity route
- Source metadata is stored on the position document but does NOT affect form fields
- No side effects: no watchlist disable, no cascade-delete (those belong to the from-activity route)

## Files Changed
- `web/app.py` — `api_add_position` route accepts optional `source_activity_id`
- `web/templates/symbol_detail.html` — removed `applyAlertPrefill()`, updated label and submit logic
