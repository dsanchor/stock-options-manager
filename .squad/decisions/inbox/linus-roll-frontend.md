# Decision: Roll Position Frontend — Conditional Buttons + Closing Source Display

**Author:** Linus (Quant Dev)
**Date:** 2025-07-15
**Status:** Implemented

## Context
Monitor agents (`open_call_monitor`, `open_put_monitor`) generate roll signals but the decision detail page only had an "Open Position" button. We needed the frontend to distinguish between watch agent signals (open) and monitor agent signals (roll).

## Decision
- Button type in `decision_detail.html` is determined by `decision.agent_type` at render time via Jinja conditional. No runtime JS sniffing.
- Roll button calls `POST /roll-from-decision/` (Rusty's endpoint, in progress). Open button still calls `POST /from-decision/`.
- `symbol_detail.html` expandable rows now show `closing_source` and `rolled_from`/`rolled_to` metadata when present. These fields are populated by Rusty's roll-position backend.

## Impact
- **Frontend ready before backend** — the roll button will 404 until Rusty's endpoint lands. This is intentional; the button only renders for monitor agent signals which don't exist yet in prod.
- All existing "Open Position" behavior for watch agents is unchanged.

## Team Notes
- Rusty: the frontend expects the same response shape from `/roll-from-decision/` as `/from-decision/`. Position objects should include `closing_source`, `rolled_from`, `rolled_to` fields for the symbol detail page to render them.
