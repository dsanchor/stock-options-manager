# Decision: Dashboard tables show activity timeline badges instead of count columns

**Author:** Rusty  
**Date:** 2026-07  
**Status:** Implemented

## Context
The dashboard agent tables previously showed Today/7d/30d alert count columns which were low-signal — a count of "3" doesn't tell you *what* happened. The activity feed below already had rich colored badges.

## Decision
- Replaced 3 count columns with a single "Recent" column containing up to 3 activity badges (oldest→newest) per row.
- Badges use the same `badge-{{ activity | lower }}` CSS classes as the activity feed for consistency.
- Each badge links to its activity detail page and has a timestamp tooltip.
- Removed the top summary cards for Alerts Today/7d/30d (kept Symbols Watched + Open Positions).
- `grand_totals` dict is preserved in the backend for any future use but no longer rendered.

## Trade-offs
- Loses exact numeric counts per time range (can still be seen in the activity feed with filters).
- Gains at-a-glance pattern recognition: seeing `[WAIT] › [WAIT] › [SELL]` is immediately actionable.
