### 2026-09-06: Split Watchlist into Portfolio and Watchlist lists
**By:** Copilot (via Copilot)
**What:** The Watchlist page must render two separate, mutually exclusive lists: Portfolio securities (symbols with Portfolio ledger presence) and true Watchlist securities (manually added symbols without Portfolio presence). Portfolio classification takes precedence so a symbol is never duplicated, even if agents or notifications are later enabled.
**Why:** User wants a clear visual distinction between owned/historical Portfolio symbols and symbols tracked only in Watchlist.
