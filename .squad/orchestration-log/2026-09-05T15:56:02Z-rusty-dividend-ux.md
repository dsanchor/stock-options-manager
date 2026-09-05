# Rusty — Dividend Portfolio UX & Navigation Design

**Timestamp:** 2026-09-05T15:56:02Z  
**Mode:** Sync (UX/Integration)  
**Role:** Frontend/UX Agent, Navigation & Interaction Design  
**Status:** ✅ Complete

---

## Outcome

Designed the complete **user interface and navigation architecture** for dividend portfolio management. Delivers independent Portfolio menu with four subpages, comprehensive form flows for all movement types, mobile/accessibility patterns, and API/BFF surface.

**Key UX Decisions:**
- **Navigation:** New top-level "Portfolio" dropdown between Economics and Chat, peer to Symbols
- **Four subpages:** Securities (Holdings), Movements (Ledger), Dividends (Yield focus), Accounts (Setup)
- **No rename of Symbols/Watchlist:** Watchlist remains option-centric; Portfolio is distinct "what I own" view
- **Ledger-centric forms:** Movement form adapts per type (BUY/SELL/DIVIDEND/DIVIDEND+STOCK); atomic submission for mixed dividends
- **Withholding prominence:** Destination WHT status badges (⚠️ Pending / ✓ Collected); null vs. zero distinction rendered explicitly
- **FX rate fetching:** Button in forms with date-smart ECB fallback; always user-overridable
- **Mobile support:** Full-screen sheets instead of slide-overs; numeric keyboards; sticky summaries
- **Accessibility:** ARIA labels on all computed fields, focus management, keyboard navigation, color + icon status indicators

---

## Design Outputs

**Primary Document:** `.squad/decisions/inbox/rusty-dividend-portfolio-ux.md` (634 lines)

**Sections:**
1. **Context & Constraints:** Directive requirements, broker/currency scope, deferred features
2. **Navigation Architecture:** TopNav menu, mobile patterns, Symbols/Watchlist rename decision
3. **Subpage Layouts:** Securities (holdings table), Movements (ledger + void), Dividends (WHT focus), Accounts (broker profiles)
4. **Movement Form Flows:** BUY, SELL, DIVIDEND (cash), DIVIDEND+STOCK (scrip/mixed); per-type field adaptations
5. **Accessibility & Error Handling:** ARIA patterns, field validation, FX fetch failure handling, mobile considerations
6. **API/BFF Surface:** RESTful endpoints, TypeScript DTOs, BFF route patterns

**Cross-references:** Integrated with Danny's architecture and Livingston's persistence model

---

## Navigation & Information Architecture

### Top-Level Menu (Current → Proposed)
```
Before:  Dashboard | Symbols ▾ | Economics | Chat | Screener ▾ | Settings ▾
After:   Dashboard | Symbols ▾ | Economics | Portfolio ▾ | Chat | Screener ▾ | Settings ▾
```

**Portfolio Dropdown Structure:**
- **Securities** (`/portfolio/securities`) — Holdings per account; read-only derived view
- **Movements** (`/portfolio/movements`) — Full transaction log with soft-void capability
- **Dividends** (`/portfolio/dividends`) — Dividend events grouped by ticker/period with WHT status
- **Brokers & Accounts** (`/portfolio/accounts`) — Broker profile setup; prerequisite onboarding

### Mobile Pattern
- Uses existing `MobileSection` component for Portfolio section
- No structural changes to `TopNav.tsx`; purely additive
- Form opens as full-screen sheet (not half-sheet) on small screens

---

## Form Flows: Type-Specific Adaptations

### BUY Form
**Fields:** Symbol → Broker → Date → Shares → Price (currency) → Fees → FX Rate → EUR equivalent → Summary

**Computed:** Total cost = (shares × price + fees) / fxRate; Avg cost/share EUR  
**Validation:** Ticker not required in watchlist (warns only); FX required if currency ≠ EUR  
**FX Button:** "🔄 Fetch" calls `/api/portfolio/fx-rate?currency=USD&date=...`

### SELL Form
**Extends BUY** with:
- Origin WHT % (some countries withhold on capital gains)
- Destination WHT % + "collected?" checkbox
- Proceeds summary (gross − fees − withholding)
- Informational hint: "Average cost basis for this ticker: €X.XX/share — Estimated gain: €Y (deferred)"

**Note:** No cost-basis lock-in on entry; deferred analytics in Phase 2

### DIVIDEND (Cash) Form
**Fields:** Ex-date → Payment date (required) → Gross/share → Shares at ex-date → Gross total → Origin WHT % + amount → Destination WHT % + "collected?" → FX Rate → Net received EUR → Notes

**Auto-populations:**
- Origin WHT % pre-filled from security country + DTA mapping (15% US, 19% ES, etc.)
- Shares at ex-date pre-filled from current position if known (editable)
- FX Rate fetches for payment date (not trade date)

**Smart chips:** "DTA: reduced to 15%" (non-blocking, informational)  
**Amber warning (if dest WHT% > 0 and uncollected):** "Will appear as pending in Dividends view"

### DIVIDEND + STOCK (Mixed/Scrip) Form
**Extends DIVIDEND (Cash)** with second section:

**Stock Leg:**
- Shares received (fractional supported)
- Price at ex-date
- Cost basis type: ◉ Zero (scrip) | ○ Fair value (elected in-lieu)
- Cost basis EUR (locked if Zero; computed if Fair value)

**Submission:** Both legs submitted atomically; if either fails, neither persisted  
**UI:** Linked by `linkedMovementId`; expanded row shows both legs with visual connector  
**Zero-cost guard:** Non-blocking tooltip explaining Spanish tax implications

---

## Holdings Table (Securities Page)

**Columns:** Account | Ticker | Name | Shares | Avg Cost/share | Avg Cost EUR | Total Cost EUR | Status (Active/Liquidated)

**Filters:** Broker multi-select | Status toggle | Text search (ticker/name)  
**Click behavior:** Expands inline or navigates to `/portfolio/securities/:ticker?accountId=` (filtered movements)  
**Add security button:** Quick-add dialog that creates a BUY movement (cost basis always ledger-derived)  

**MVP deferred:** Current price, unrealized P&L, yield on cost (requires live price feed)

---

## Movements Table (Transaction Log)

**Columns:** Date | Account | Type (pill: BUY/SELL/DIV/DIV+STK/VOIDED) | Ticker | Qty (negative for SELL) | Price | Currency | FX Rate | Gross (native) | Fees | Origin WHT | Dest WHT | Net EUR | Notes | Actions

**Filters:** Date range | Account(s) multi-select | Type(s) | Currency | Ticker text  
**Pagination:** 50 rows/page; load-more or cursor  
**Row click:** Expands detail panel with full fields + edit/void actions  
**Void workflow:**
1. Click "Void" → Modal: "Reason (required):"
2. PATCH `/api/portfolio/movements/:id/void` with reason
3. Row marked VOIDED (greyed, strikethrough), reason visible on expand
4. Cost-basis auto-recalculated
5. If voiding a DIV with linked stock leg: warning "This also voids the linked stock leg"

**Immutability:** No in-place edit of financial fields; void + re-enter required

---

## Dividends Table (Dividend Events Log)

**Columns:** Payment Date | Ex-Date | Account | Ticker | Gross/Share | Shares | Gross Total (native+EUR) | Origin WHT % | Origin WHT EUR | Dest WHT % | Dest WHT Status (Collected/⚠️ Pending/—) | Net EUR | Type (Cash/Mixed)

**Summary stats (above table):**
- Total gross received YTD (EUR)
- Total origin WHT paid YTD (EUR)
- **Destination WHT pending (EUR)** — amber highlight
- Net dividends YTD (EUR)

**Filters:** Year (default: current) | Account(s) | Ticker | Month | Type (Cash/Mixed/All) | WHT status  
**No separate add button:** DIV movements added via Movements form

---

## Broker Accounts Setup

**List view:** Card per account with broker logo, nickname, default currency, holdings count, Edit/Remove actions  
**Add/Edit form:**
```
Broker              [Fidelity | HeyTrade | ING | IBKR] dropdown
Nickname            [text, required, e.g. "ING Principal"]
Default currency    [EUR | USD | GBP | CHF] (locked for broker-specific, e.g. HeyTrade→EUR)
Notes               [optional text area]
```

**Broker-to-currency mapping (advisory):** Fidelity→USD, HeyTrade→EUR, ING→EUR, IBKR→user-selectable

---

## Form Validation & Error Handling

**Field-level:** Inline below field, red border, `aria-describedby`  
**Server errors:** Toast (Sonner pattern) for non-blocking; inline banner for validation failures  
**FX Rate fetch failure:** Warning in FX section — "Could not fetch rate. Enter manually." (field stays required)  
**Void failure:** Toast error, row not marked voided  
**Network pattern:** Optimistic updates (status pill → "…"), rollback on failure, specific error via toast

---

## Accessibility Standards

- **Labels:** All form controls have `<label>` elements (not placeholder-only)
- **Computed fields:** `aria-live="polite"` so screen readers announce updates
- **Movement type selector:** `role="tablist"` / `role="tab"` or segmented `<fieldset>`
- **Slide-over:** `role="dialog"`, `aria-modal="true"`, focus-trap, Escape closes with "discard?" confirmation
- **Status indicators:** Color + icon (never color alone); ⚠️ for Pending, ✓ for Collected
- **Keyboard:** Full navigation; FX Fetch button has descriptive `aria-label`

---

## Mobile Optimizations

- Form: full-screen sheet (not half-sheet) on viewport < 768 px
- Numeric inputs: `inputmode="decimal"` for mobile keyboards
- FX Fetch button: icon-only on very narrow screens (≤ 375 px)
- Tables: horizontal scroll with sticky left columns; card-list layout below 640 px
- Summary section: sticky bottom on sheet so it's visible while scrolling

---

## API Surface (Proxy to Backend)

All BFF routes under `/api/portfolio/`, following existing pattern:

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/portfolio/accounts` | List broker profiles |
| POST | `/api/portfolio/accounts` | Add broker profile |
| PUT | `/api/portfolio/accounts/:id` | Update broker profile |
| DELETE | `/api/portfolio/accounts/:id` | Remove (guard: reject if movements exist) |
| GET | `/api/portfolio/securities` | Holdings (derived) |
| GET | `/api/portfolio/movements` | Ledger with pagination |
| POST | `/api/portfolio/movements` | Create movement(s) |
| PATCH | `/api/portfolio/movements/:id/void` | Soft-delete with reason |
| GET | `/api/portfolio/dividends` | Dividend events log |
| GET | `/api/portfolio/dividends/summary` | Stat cards |
| GET | `/api/portfolio/fx-rate` | `?currency=USD&date=2026-01-15` |

---

## Assignments to Team

- **Danny:** Architecture validation for domain boundaries and navigation placement
- **Livingston:** Persistence mapping for all form field sets and validation rules
- **Frontend implementation (future):** TypeScript components, Sonner toast patterns, form state (React Hook Form/Zod)

---

## Deferred to Phase 2+

- Excel import UI (batch uploader, conflict resolution)
- Charts & time-series visualizations
- Economics integration (dividend income stream)
- Fiscal export UI & tax reporting
- Current price/unrealized P&L feeds

---

## Next Steps

1. Confirm mobile layout with design team (card-list vs. horizontal scroll tradeoffs)
2. Finalize broker logo sources and color scheme integration
3. Implement TypeScript DTOs and BFF routes
4. Begin frontend component build-out (SecurityHoldingsTable, MovementForm, DividendLog)

---

**UX/Frontend Agent:** Rusty  
**Date:** 2026-09-05T15:56:02Z  
**Verdict:** ✅ **UX DESIGN COMPLETE — READY FOR IMPLEMENTATION**
