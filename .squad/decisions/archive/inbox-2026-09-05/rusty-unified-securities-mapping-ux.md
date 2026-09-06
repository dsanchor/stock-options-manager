# Unified Securities Catalog & Blocking Company Mapping — UX Design

**Author:** Rusty (Agent Dev — frontend/integration)  
**Date:** 2026-09-05  
**Directive:** `copilot-directive-20260905T171218+0200.md`  
**Updates:** `rusty-dividend-import-ux.md`, `rusty-purchase-import-ux.md`, `rusty-sales-import-ux.md` — replaces the inline `unresolved_security` warning with a dedicated blocking Company Resolution phase; adds unified Securities catalog design.  
**Status:** Design only — no production code. No user financial data reproduced.

---

## 1. Authoritative Change: Company Resolution Is a Hard Gate

The previous import designs treated `unresolved_security` as a per-row blocking warning — the row was marked `🔴 Blocking` and excluded from import. This created the wrong mental model: it implied that importing the other rows and ignoring unresolved companies was a clean outcome. In reality, a company-to-security mapping is a prerequisite for the entire row's ledger existence.

**New rule, effective for all three import types (dividends, purchases, sales):**

> A row may not be imported — and does not reach the row preview table — until its `company_raw` value is resolved to a canonical `security_id` in the security catalog, OR all rows for that company are explicitly excluded by the user.

This changes the wizard flow from 4 steps to 5:

```
Type → Upload → Metadata → Company Resolution → Row Preview → Confirm
```

The Company Resolution phase is a dedicated screen that must be fully satisfied before the row preview renders. The `unresolved_security` blocking warning code is retired from the row preview and replaced by this gate.

---

## 2. Company Resolution Phase (New Step 2)

### 2.1 Screen Layout

Shown after Batch Metadata. Displays all unique `company_raw` values in the batch, grouped by resolution status.

```
┌──────────────────────────────────────────────────────────────────┐
│  Step 2 of 4 — Resolve Companies                                 │
│  {N} companies in this file · {A} resolved · {B} suggestions    │
│  · {C} unresolved                                                │
│                                                                  │
│  ── ✅ Resolved ({A} companies, {X} rows) ─────────────────────  │
│  [Collapsed by default — click to expand]                        │
│                                                                  │
│  ── 💡 Suggestions — confirm to proceed ({B} companies) ────────  │
│                                                                  │
│  "Coca-Cola" (12 rows)                                           │
│   → KO — The Coca-Cola Company | NYSE | USD                     │
│     Confidence 94% · "Name match + known US ticker"             │
│     [ ✓ Confirm KO ]  [ 🔍 Search instead ]  [ ✕ Exclude rows ] │
│                                                                  │
│  "Iberdrola Renovables" (4 rows)                                 │
│   → IBE — Iberdrola S.A. | BME/XMAD | EUR                      │
│     Confidence 81% · "Company name contains registered brand"   │
│     [ ✓ Confirm IBE ]  [ 🔍 Search instead ]  [ ✕ Exclude rows ]│
│                                                                  │
│  [ ✓ Confirm all suggestions ≥ 85% confidence (1 company) ]     │
│                                                                  │
│  ── 🔴 Unresolved — must map or exclude ({C} companies) ────────  │
│                                                                  │
│  "ENAGAS SA" (7 rows)                                           │
│   No match found.                                                │
│   [ 🔍 Map to existing security ]  [ ➕ Create security ]         │
│   [ ✕ Exclude all 7 rows ]                                      │
│                                                                  │
│  "Unknown Ventures Ltd" (2 rows)                                 │
│   No match found.                                                │
│   [ 🔍 Map to existing security ]  [ ➕ Create security ]         │
│   [ ✕ Exclude all 2 rows ]                                      │
│                                                                  │
│  ─────────────────────────────────────────────────────────────── │
│  [ ← Back ]         [ Continue to Preview → ] (disabled until   │
│                       all companies are resolved or excluded)    │
└──────────────────────────────────────────────────────────────────┘
```

### 2.2 Resolution Status Definitions

| Status | Chip | Condition | Blocks "Continue"? |
|---|---|---|---|
| **Auto-resolved** | `✅` | Exact alias match in saved alias store | No — collapsed by default |
| **Suggestion — confirmed** | `✅` | User clicked "Confirm" on a suggestion | No |
| **Suggestion — pending** | `💡` | AI/fuzzy suggestion awaiting user confirmation | **Yes** |
| **Unresolved — excluded** | `✕` | User clicked "Exclude all rows" | No |
| **Unresolved — unmapped** | `🔴` | No suggestion; not yet mapped or excluded | **Yes** |

"Continue to Preview" is disabled until every company is either `✅` (auto or confirmed) or `✕` (excluded). The button shows a disabled tooltip: "Resolve or exclude {N} companies to continue."

### 2.3 Auto-Resolved Section (Collapsed)

Companies resolved via saved aliases are collapsed by default to reduce visual noise. The section header shows the aggregate count. Clicking expands a read-only list:

```
✅ Resolved (3 companies, 48 rows)
  TELEFÓNICA S.A. (22 rows) → TEF | BME/XMAD | EUR  [Override]
  REPSOL SA       (18 rows) → REP | BME/XMAD | EUR  [Override]
  IBERDROLA SA    ( 8 rows) → IBE | BME/XMAD | EUR  [Override]
```

"[Override]" opens the Map/Create flow for that company, replacing the auto-resolved mapping.

### 2.4 AI/Fuzzy Suggestion Display

Each suggestion card shows:

```
"Coca-Cola" (12 rows)
╔═════════════════════════════════════════════════════════╗
║  Suggested:  KO                                         ║
║  Name:       The Coca-Cola Company                      ║
║  Exchange:   NYSE (XNYS)    Currency: USD               ║
║  Confidence: 94%                                        ║
║  Reason:     "Name match: 'Coca-Cola' → 'The Coca-Cola  ║
║               Company'; ticker KO confirmed in catalog"  ║
╚═════════════════════════════════════════════════════════╝
[ ✓ Confirm KO ]    [ 🔍 Search instead ]    [ ✕ Exclude rows ]
```

**Confidence thresholds (advisory — never automatic):**
- ≥ 85%: shown with green accent; eligible for "Confirm all" bulk action
- 60–84%: shown with amber accent; requires individual confirmation
- < 60%: shown with muted style; "Search instead" is the recommended action

**No automatic confirmation at any confidence level.** The "Confirm all ≥ 85%" bulk button is the only accelerator, and it still requires a single user click per batch.

After confirming, the card collapses to the auto-resolved style:
```
✅ Confirmed: KO — The Coca-Cola Company | NYSE | USD   [Change]
```

### 2.5 "Map to Existing Security" Flow

Opens an inline combobox below the company card (does not navigate away):

```
"ENAGAS SA" — Map to existing security
┌─────────────────────────────────────────────────────────┐
│  🔍 Search: [type ticker, ISIN, or name...]              │
│  ─── Results ─────────────────────────────────────────── │
│  ENG  Enagás S.A.          BME/XMAD  EUR               │
│  GAS  Natural Gas ETF      NYSE      USD                │
│  ─── Not found? ──────────────────────────────────────── │
│  [ ➕ Create "Enagás S.A." as a new security ]            │
└─────────────────────────────────────────────────────────┘
```

The search queries `GET /api/portfolio/securities/search?q=ENAGAS` against the full security catalog. Selecting a result maps the company and collapses the card to `✅ Mapped: ENG | BME/XMAD | EUR [Change]`.

### 2.6 "Exclude All Rows" Action

Clicking "✕ Exclude all {N} rows" for a company:
1. Collapses the card to a muted state: `✕ Excluded — 7 rows will not be imported [Undo]`
2. Removes the company from the "blocks continue" list
3. The excluded rows appear in Step 4 (Confirm) as "Excluded by company resolution"
4. Can be undone at any time before "Continue to Preview" is clicked

---

## 3. AI / Fuzzy Suggestion Service

### 3.1 How Suggestions Are Generated

The suggestion service runs server-side (or via a background BFF call) immediately after parse completes, in parallel with the Batch Metadata display. By the time the user arrives at Company Resolution, suggestions are already ready.

**Resolution chain (evaluated in order, first match wins):**

1. **Exact alias match** (`company_raw_normalized → security_id` in saved alias store) → auto-resolved, ≥99% confidence
2. **Exact catalog match** (normalized name matches a `security.display_name` or `security.ticker`) → auto-resolved, 99% confidence
3. **AI name-to-ticker inference** (LLM agent call with structured output: `{ticker, name, exchange, currency, confidence, reason}`) → suggestion, confidence from model
4. **Fuzzy string match** (trigram similarity against catalog names + known ticker databases) → suggestion, confidence = similarity score

Steps 3 and 4 are non-blocking — if they time out (> 3 seconds), the company appears as "Unresolved" with a note: "Could not fetch a suggestion — map manually or try again." A `[Retry suggestion]` button is shown.

### 3.2 Suggestion API

```
POST /api/portfolio/securities/suggest
Body: { company_names: string[] }
Response: {
  results: {
    company_raw: string;
    resolved?: SecurityRef;     // present for auto-resolved
    suggestions: Array<{
      security_id?: string;     // null if candidate not yet in catalog
      ticker: string;
      name: string;
      exchange_mic: string;
      exchange_label: string;
      currency: SupportedCurrency;
      confidence: number;       // 0–100
      reason: string;
    }>;
  }[]
}
```

This endpoint does not modify any data. Confirmation of a suggestion calls `POST /api/portfolio/security-aliases` (same alias persistence endpoint as before, now also called from the Company Resolution phase).

---

## 4. Create Security Form

Shown when user clicks "Create security" for an unresolved company. Opens as an inline panel below the company card (not a separate modal — keeps context visible).

```
┌─────────────────────────────────────────────────────────────────┐
│  Create new security for "ENAGAS SA"                            │
│                                                                  │
│  Ticker symbol *      [text, e.g. ENG]                          │
│                       Unique identifier in this system          │
│                                                                  │
│  Exchange *           [BME — Bolsa de Madrid (XMAD)         ▾] │
│    NYSE — New York (XNYS)        USD                            │
│    NASDAQ — NASDAQ (XNAS)        USD                            │
│    BME — Bolsa de Madrid (XMAD)  EUR                            │
│    AMS — Euronext Amsterdam (XAMS) EUR                          │
│    LSE — London Stock Exchange (XLON) GBP                       │
│    SWX — SIX Swiss Exchange (XSWX)   CHF                       │
│    ─── Other ────────────────────────────────────────────────── │
│    Enter MIC code manually: [____]                              │
│                                                                  │
│  Currency *           [EUR ▾]  (auto-suggested from exchange)   │
│                                                                  │
│  Display name *       [pre-filled: "ENAGAS SA", editable]       │
│                                                                  │
│  ── Optional ──────────────────────────────────────────────────  │
│  ISIN                 [text, e.g. ES0130960018]                  │
│  Provider symbols:    IBKR conid  [_______]                     │
│                       Bloomberg   [_______]                     │
│                       Yahoo/other [_______]                     │
│                                                                  │
│  [ Cancel ]                    [ Create & map → ]               │
└─────────────────────────────────────────────────────────────────┘
```

On "Create & map": calls `POST /api/portfolio/securities` then `POST /api/portfolio/security-aliases` in sequence. On success the card collapses to `✅ Created & mapped: ENG — Enagás S.A. | BME/XMAD | EUR [Edit]`.

**Exchange list is extensible** — the enum is a server-side configuration. The "Enter MIC code manually" escape hatch covers any exchange not in the standard list. The MIC is validated against the ISO 10383 format (`[A-Z]{4}`).

**Currency auto-suggestion by exchange:**

| Exchange | Auto-suggested currency | Overridable? |
|---|---|---|
| NYSE, NASDAQ | USD | Yes (e.g., ADR listed in USD but reports in EUR) |
| BME | EUR | Yes |
| AMS (Euronext) | EUR | Yes |
| LSE | GBP | Yes (some LSE listings are USD or EUR) |
| SWX | CHF | Yes |
| Manual MIC | No auto-suggestion | User selects |

---

## 5. Row Preview — After Company Resolution

Once all companies are resolved or excluded, "Continue to Preview" advances to the Row Preview (formerly Step 2, now Step 3). The preview table is unchanged from the individual import designs with one key difference:

**The Security column now shows the resolved security** (ticker + exchange badge) for every row — no `⚠️ "Unknown: {company_raw}"` cells and no per-row [Map] button. Those are replaced by the Company Resolution phase.

**Excluded company rows** appear in a collapsed "Excluded rows" section at the bottom of the preview table, showing a count and the reason: "Excluded — company not resolved." They are not interleaved with importable rows and cannot be checked.

The row-level `unresolved_security` warning code is retired. The per-row warning chips in the preview table no longer include it.

---

## 6. Unified Securities Catalog

### 6.1 Motivation

The current system has two separate security concepts:
1. **Symbols** (`/symbols`) — the options-agent watchlist, option-centric (DGI, calls, puts). `total_shares` is a manually set field.
2. **Portfolio Securities** (`/portfolio/securities`) — ledger-derived holdings, separate from the watchlist.

The directive calls for a **unified security catalog** where every security exists once, with different data attributes populated based on usage (options tracking, portfolio holdings, or both). A security may have zero holdings and still be visible in the catalog.

### 6.2 Unified Data Model (Conceptual)

A `security` record has:

| Attribute group | Source | Notes |
|---|---|---|
| **Identity** (ticker, name, exchange, currency, ISIN) | User entry or suggestion | Canonical; required |
| **Options-tracking data** (DGI score, tech timing, momentum, entry tag, category) | Options agent / enrichment | Present only if security is in the watchlist |
| **Options-agent flags** (watch_calls, watch_puts, pause_until_earnings) | User settings | Options-specific toggles; unchanged |
| **Ledger-derived holdings** (shares owned per account) | Portfolio ledger replay | Derived, not stored; may be 0 or absent |
| **Portfolio metadata** (first bought, last transaction date) | Derived | |

### 6.3 Navigation: Symbols → Securities (Incremental Rename)

**Recommended transition — additive, not breaking:**

The top-level nav item "Symbols" is renamed to "Securities" with its dropdown updated:

```
Before:                          After:
Symbols ▾                        Securities ▾
  Watchlist  → /symbols            Securities   → /symbols   (same route)
  Calendar   → /symbols/calendar   Calendar     → /symbols/calendar
  Action Plans → /plans            Action Plans → /plans
```

The `/symbols` route and all child routes (`/symbols/:ticker`, `/symbols/calendar`) are **unchanged**. No redirect, no broken links. The route stays `/symbols`; only the nav label changes. This is a single-line change in `DROPDOWNS` in `TopNav.tsx`.

**The Watchlist sub-item is renamed to "Securities"** — it leads to the same `/symbols` page, which is extended (see §6.4). The word "Watchlist" disappears from the nav but the page content and URL are stable.

**Symbol detail routes** (`/symbols/:ticker`) are preserved exactly as-is. The options toggles, activity feeds, plan views, and all existing functionality on the detail page are unchanged.

### 6.4 Extended `/symbols` Page (Securities View)

The existing `SymbolsTable` component gains additional columns and a filter, while preserving all current columns and behaviors.

**New columns added (right-side additions):**

| Column | Data source | Behavior for securities with no holdings |
|---|---|---|
| Exchange | `security.exchange_label` | Shows for all securities |
| Currency | `security.currency` | Shows for all securities |
| Shares owned | Ledger-derived (summed across all accounts) | Shows `—` or `0` |

**Changed column:**

| Column | Current behavior | New behavior |
|---|---|---|
| Shares (`total_shares`) | Manually set integer | **Read-only**; ledger-derived; replaces the inline edit affordance |

The inline-edit affordance on Shares (currently a click-to-edit input) is removed. Shares is now a derived aggregate. To change share count, the user creates a movement (BUY/SELL) — not a direct edit on the security.

**New filter:**

```
[ All Securities ]  [ Owned only ]
```
- "All Securities" (default): shows every security in the catalog (option-tracked + portfolio-held + both + neither)
- "Owned only": filters to ledger-derived shares > 0

**Securities with no holdings but with options tracking data** continue to appear in "All Securities" with `—` in the Shares column. They are not hidden or demoted.

**The current suitability filter pills** (All, Ideal Puts, Ideal Calls, No Puts, No Calls) continue to function unchanged in the "All Securities" view. In "Owned only" view they also apply — the two filters stack.

### 6.5 Holdings Page Relationship

| Page | URL | Description |
|---|---|---|
| **Securities** (unified catalog) | `/symbols` | All securities; options + portfolio data; superset |
| **Holdings** | `/portfolio/securities` | Ledger-derived positions; owned-only; organized by account |

Holdings at `/portfolio/securities` is a filtered, account-organized subset of the same underlying security catalog. It does not duplicate security metadata — it derives it. The two pages coexist; Holdings is the portfolio-first view; Securities is the research-first view.

A security that has ledger holdings but is NOT in the watchlist (no options tracking data) appears in Holdings normally, and in Securities with `—` for DGI/momentum/entry-tag columns. It is not hidden in either view.

---

## 7. Import Flow with Unified Catalog

### 7.1 "Map to existing" now searches the unified catalog

The security search in the Company Resolution phase queries the full security catalog — not just the portfolio holdings or the options watchlist. A security registered via a previous import (or via manual entry in the catalog) is immediately available as a mapping target in a new import, even if it has no transactions yet.

### 7.2 "Create security" adds to the unified catalog

Creating a security during import adds it to the canonical security catalog. It immediately appears in the Securities view (`/symbols`) with `—` in the Shares, DGI, Momentum columns. When the import commits, ledger-derived shares are populated via replay.

**What creation does NOT do:**
- **No auto-toggle of options-agent tracking.** `watch_calls`, `watch_puts`, and `pause_until_earnings` all default to OFF. The user enables these explicitly from the Security detail page — creating a security during import does not enrol it in the options agent, does not create alert rules, and does not trigger data enrichment.
- **No implied ownership.** A security created with no committed movements does not appear in Holdings and has `total_shares = 0`. It becomes a holding only when at least one committed ledger transaction references it and ledger replay runs.
- **No route changes.** The security becomes queryable at `/symbols/:ticker` once created, using the existing route — no new routes, no redirects. `/symbols`, `/economics`, options detail pages, and all existing child routes are unchanged.

### 7.3 Alias persistence (unchanged mechanics, expanded scope)

The saved alias store (`company_raw_normalized → security_id`) covers all three import types. A mapping made during dividend import is pre-resolved during purchase import. Because security IDs now come from the unified catalog, aliases point to the same canonical record regardless of which import type created it.

---

## 8. Accessibility

### Company Resolution Screen

- The screen is a single `<section>` with `aria-labelledby="company-resolution-heading"`.
- Each company group is a `<div role="group" aria-labelledby="{company-id}-heading">` where the heading includes the company name and row count.
- The suggestion confidence badge uses `aria-label="Confidence {N}%"` — not color-alone.
- "Confirm" button: `aria-label="Confirm {company_raw} maps to {ticker} — {name}"`.
- "Confirm all ≥ 85%" button: `aria-label="Confirm {N} suggestions with confidence 85% or higher"`. Has a `role="button"` with `aria-describedby` pointing to a live region that announces how many were just confirmed.
- "Exclude all rows" button: `aria-label="Exclude all {N} rows for {company_raw} from this import"`.
- After confirming or excluding, focus moves to the next unresolved company's heading (or to "Continue to Preview" if none remain).
- The "Continue to Preview" button has `aria-disabled="true"` (not `disabled`) when blocked, so it remains focusable with a screen-reader announcement of the blocking reason: "Resolve or exclude {N} remaining companies to continue."
- The security search combobox uses `role="combobox"`, `aria-autocomplete="list"`, `aria-expanded`, and `aria-controls` pointing to the results `role="listbox"`.
- The Create Security inline form uses `aria-modal="false"` (it is inline, not a dialog) and `aria-live="polite"` on the confirmation message.

### Bulk Alias Confirmation

- "Confirm all" renders as a standard `<button>`, not a checkbox, to avoid ambiguity with "select all".
- After activation, an `aria-live="assertive"` announcement: "{N} companies confirmed. No unconfirmed suggestions remain."
- The confirmed cards announce their state change via `aria-live="polite"` individually for screen readers that may not have seen the bulk action.

---

## 9. Warning Taxonomy Updates

### Retired warning codes (all import types)

| Code | Previously | Replaced by |
|---|---|---|
| `unresolved_security` (blocking) | Per-row blocking chip in preview | Company Resolution phase gate — never reaches preview |

### New warning codes (Company Resolution phase only)

| Code | Shown where | Condition | User action |
|---|---|---|---|
| `suggestion_pending` | Company card | AI/fuzzy suggestion not yet confirmed | Confirm or Search |
| `company_unresolved` | Company card, "Continue" tooltip | No suggestion and not mapped/excluded | Map or Create or Exclude |
| `suggestion_timeout` | Company card | Suggestion service timed out | Retry or Map manually |
| `company_excluded` | Preview excluded section | User excluded all rows for company | Undo (pre-commit) |

### Unchanged warning codes

All data-quality warnings from the three import designs (`arithmetic_mismatch`, `negative_inventory`, `corporate_action_pending`, `duplicate`, `commission_exceeds_proceeds`, etc.) continue unchanged in the Row Preview table. Security resolution is no longer one of them.

---

## 10. API Surface Updates

```
GET    /api/portfolio/securities/search
       ?q={text}&limit=10
       Searches unified catalog by ticker, name, ISIN.
       Returns: SecurityRef[] sorted by relevance.
       Used by "Map to existing" search combobox and global symbol search.

POST   /api/portfolio/securities/suggest
       Body: { company_names: string[] }
       Response: per-company suggestion list with confidence + reason.
       Backed by: catalog fuzzy match + optional LLM agent inference.
       Timeout: 3s per company; unresolved names return empty suggestions.

POST   /api/portfolio/securities
       Body: CreateSecurityRequest (see §10.1)
       Creates a new security in the unified catalog.
       Returns: SecurityRef

GET    /api/portfolio/securities/:id
       Returns full SecurityRecord including options-tracking data (if present)
       and ledger-derived holdings summary.

GET    /api/symbols/overview
       Extended: now returns all securities (catalog), not just watchlist.
       New fields: exchange_mic, exchange_label, currency, ledger_shares_owned.
       `total_shares` field now reflects ledger-derived value (may differ from
       previously manually stored value during transition period).

PATCH  /api/symbols/:symbol
       PUT behavior unchanged for options-tracking data.
       `total_shares` field now ignored on write (ledger-derived; cannot be
       manually set via this endpoint after migration). Returns 422 if attempted.
```

### 10.1 CreateSecurityRequest DTO

```typescript
interface CreateSecurityRequest {
  ticker: string;                   // required; unique in catalog
  display_name: string;             // required
  exchange_mic: string;             // required; ISO 10383 MIC, e.g. "XMAD"
  exchange_label?: string;          // human label; derived from MIC if omitted
  currency: SupportedCurrency;      // required
  isin?: string;                    // optional
  provider_symbols?: {
    ibkr_conid?: string;
    bloomberg?: string;
    yahoo?: string;
    other?: string;
  };
}

interface SecurityRef {
  id: string;
  ticker: string;
  display_name: string;
  exchange_mic: string;
  exchange_label: string;
  currency: SupportedCurrency;
  isin?: string;
}
```

---

## 11. Transition Considerations

### `total_shares` Migration

The current `SymbolRow.total_shares` is a manually set integer per symbol. After unification, it becomes ledger-derived. During a transition period before any ledger data exists (Phase 1, before any import has run), the manually set value is preserved as a fallback display value. Once any movement is committed for a security, ledger-derived shares takes precedence and the manual value is ignored.

A migration banner appears on the Securities page after Phase 1.5 import capability ships:

```
ℹ️  Shares owned is now ledger-derived. Previously manually set values
are shown as a fallback until portfolio movements are imported.
[ Learn more ] [ Import history → ]
```

### No Breaking URL Changes

- `/symbols` → unchanged (route stays, page title stays "Symbols" or becomes "Securities" — title only)
- `/symbols/:ticker` → unchanged
- `/symbols/calendar` → unchanged
- `/plans` → unchanged
- `/portfolio/securities` → unchanged (Holdings page)
- Screener routes → unchanged

The nav label "Symbols" is renamed to "Securities" and the sub-item "Watchlist" is renamed to "Securities" — both are cosmetic label changes. No redirects required.

---

## 12. Route and Phase Placement

| Route | Change |
|---|---|
| `/symbols` | Label renamed; columns added; manual share edit removed; "Owned only" filter added |
| `/portfolio/import` | Company Resolution phase inserted between Metadata and Preview |
| `/api/portfolio/securities/suggest` | New endpoint |
| `/api/portfolio/securities/search` | New endpoint (extends or replaces existing symbol search) |
| `/api/portfolio/securities` | New create endpoint |
| `/api/symbols/overview` | Extended response (exchange, currency, ledger_shares_owned) |

**Phase placement:**

| Phase | Scope |
|---|---|
| Phase 1 (MVP) | Manual BUY/SELL/DIVIDEND forms; securities created manually via Add Security form |
| Phase 1.5 | Dividend import; alias mapping; this design's Company Resolution phase |
| Phase 1b | Purchase import |
| Phase 1c | Sales import |
| **Phase 1d (this document)** | Unified catalog; Company Resolution as hard gate; AI/fuzzy suggestions; extended Securities view with ledger-derived shares; Create Security form with exchange/ISIN |
| Phase 2 | Charts, Economics integration, fiscal export |

Phase 1d is a **cross-cutting infrastructure** change — it lands in the import wizard and in the Securities view simultaneously. It may be split into two sub-phases: (a) Company Resolution gate + suggestion service, and (b) unified Securities catalog view. The gate (a) is a prerequisite for clean ledger data; (b) is a UI enhancement that can follow.

---

## Summary

**Company resolution is now a hard gate before row preview.** The import wizard gains a dedicated "Resolve Companies" step between Metadata and Preview. Every `company_raw` value must be mapped to a canonical `security_id` — via saved alias (auto), AI suggestion (confirm), or manual Map/Create — before the row preview renders. Rows for an unresolved company can be bulk-excluded instead, which removes the block without requiring a mapping.

**AI/fuzzy suggestions** show ticker, exchange, currency, confidence (0–100), and reason. No automatic confirmation at any level. Bulk "Confirm all ≥ 85%" is the only accelerator. Suggestions load asynchronously; timeout gracefully shows a retry option.

**Create Security form** supports NYSE, NASDAQ, BME/XMAD, Euronext Amsterdam/XAMS, LSE/XLON, SWX/XSWX, plus a manual MIC escape hatch. Required: ticker, exchange, currency, display name. Optional: ISIN, IBKR conid, Bloomberg, Yahoo symbols. Currency auto-suggested by exchange but overridable.

**Unified Securities catalog:** `/symbols` (now labeled "Securities") becomes the superset — options-tracked symbols + portfolio-held securities + both + registered-only. Securities with no holdings show `—` in Shares. `total_shares` becomes ledger-derived (no inline edit). An "Owned only" filter lets users narrow to held positions. `/portfolio/securities` (Holdings) remains the account-organized ledger view. No URL changes; nav labels only.

**Options toggles, symbol detail routes, and all existing screener/plan functionality are unchanged.**

**Accessibility:** Company Resolution screen is fully keyboard/screen-reader navigable; focus moves to next unresolved company after each action; bulk confirm announces via aria-live; "Continue" button uses `aria-disabled` (not `disabled`) to remain focusable with a blocking-reason announcement.
