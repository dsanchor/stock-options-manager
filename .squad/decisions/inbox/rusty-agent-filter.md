# Decision: Agent Type Filter — Dynamic Population from DOM

**Author:** Rusty  
**Date:** 2026-07  
**Status:** Implemented

## Context
We needed agent type filter dropdowns on the dashboard and symbol detail pages. Options could be passed server-side or built client-side.

## Decision
Populate the agent type dropdown options dynamically from the DOM (same pattern as the symbol filter) rather than injecting them from the server. This avoids coupling the JS to the Python `AGENT_TYPES` dict and means any new agent type automatically appears once it has activity items.

## Trade-off
If an agent type has zero recent activity, it won't appear in the dropdown. This is acceptable since filtering an absent type would yield no results anyway.

## Files
- `web/static/app.js` — filter logic + dynamic population
- `web/templates/dashboard.html` — `#activity-agent-filter` select + `data-agent-type` attribute
- `web/templates/symbol_detail.html` — `#sym-activity-agent-filter` select + `data-agent-type` attribute
