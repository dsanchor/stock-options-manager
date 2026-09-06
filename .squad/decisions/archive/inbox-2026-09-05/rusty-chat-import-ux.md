# Chat Import UX — Authoritative Design

**Author:** Rusty (Agent Dev — frontend/integration)  
**Date:** 2026-09-05  
**Directives:** `copilot-directive-20260905T172036+0200.md`, `copilot-directive-20260905T172200+0200.md`  
**Supersedes:** The multi-step wizard navigation in `rusty-dividend-import-ux.md`, `rusty-purchase-import-ux.md`, `rusty-sales-import-ux.md`, and `rusty-unified-securities-mapping-ux.md` — **as the presentation layer only**. All parser rules, warning taxonomies, domain model, API surface, and DTO definitions from those documents remain authoritative and are reused here unchanged. This document replaces only the step-wizard shell with a chat-driven shell that embeds the same deterministic components.  
**Depends on:** `rusty-unified-securities-mapping-ux.md` (Company Resolution logic, Create Security form, suggestion service), `danny-unified-security-master.md` (`security_id = MIC:ticker` canonical model)  
**Status:** Design only — no production code. No user financial data reproduced.

---

## 1. Design Principle: LLM as Orchestrator, Parser as Source of Truth

The import chat is **not a free-form conversation**. The LLM text provides:
- Contextual framing ("Here's what I found in your file")
- Natural-language grouping of questions ("Two companies need your attention")
- Acknowledgements and transition summaries

The LLM text does **not**:
- Compute row counts, totals, warnings, or classifications
- Hold import state or remember answers
- Display financial values (all amounts come from the parser)
- Execute the import or trigger ledger writes

Every number, every row count, every financial amount, every warning shown in chat is **rendered from deterministic parser state held server-side in the import session**. A card that says "142 rows ready" reads that number from the session state — the LLM does not produce it.

This distinction is visible to the user via the card/message duality: **LLM text is plain prose in the chat bubble; deterministic data lives inside styled cards** embedded in the bubble or adjacent to it. If the LLM were replaced with a rules-based orchestrator, the cards would be identical.

---

## 2. Where Chat Import Lives

Route: `/portfolio/import` — **no change**. The wizard-shell navigation is replaced by a chat interface at the same route. The breadcrumb (`Type → Upload → Metadata → Company Resolution → Preview → Confirm`) is gone. In its place: a chat thread that constitutes the import session.

The three deep-links continue to work as entry points:
- `/portfolio/import?type=dividends`
- `/portfolio/import?type=purchases`
- `/portfolio/import?type=sales`

When a deep-link type is provided, the Upload card is pre-typed and the type-detection question is skipped.

---

## 3. Session Lifecycle

```
[Start]
  ↓ Upload card appears (empty session)
  ↓ User uploads file or pastes data
  ↓ Parser runs server-side (async); session created with import_session_id
[Resolve phase — iterative]
  ↓ Assistant posts parse summary card
  ↓ Assistant posts first question batch (highest-priority ambiguities)
  ↓ User responds via interactive cards (buttons, comboboxes, toggles)
  ↓ Assistant acknowledges, updates session state, posts next question batch
  ↓ Repeat until progress = 100% (all blockers cleared or deferred/excluded)
[Final]
  ↓ Assistant posts deterministic preview totals card
  ↓ User clicks "Confirm Import"
  ↓ Server commits ledger writes; ledger replay triggered
  ↓ Post-import summary card; link to reconciliation queue
[Session archived]
```

A session in the Resolve phase can be closed and resumed. Answered questions persist. The thread is scrollable; prior answered cards remain visible but collapsed.

---

## 4. Initial State: Upload Card

On landing at `/portfolio/import`, the chat thread contains a single pinned card at the top (not a chat bubble — it is a persistent interface element):

```
┌──────────────────────────────────────────────────────────────────┐
│  📂  Import historical records                                    │
│                                                                   │
│  Drag & drop a .CSV or .XLSX file, or paste data below.          │
│  ┌──────────────────────────────────────────────────────────┐     │
│  │  (drop zone — dashed border)                             │     │
│  └──────────────────────────────────────────────────────────┘     │
│                                                                   │
│  Or paste:  ┌────────────────────────────────────────────────┐   │
│             │  (monospace textarea, 6 rows visible)           │   │
│             └────────────────────────────────────────────────┘   │
│                                                                   │
│  What are you importing?                                          │
│  [ 💰 Dividends ]  [ 🛒 Purchases ]  [ 📤 Sales ]                │
│  ← Select one, or let me detect it from your file               │
│                                                                   │
│  [ Parse file → ]                                                 │
└──────────────────────────────────────────────────────────────────┘
```

**Type detection:** if no type button is clicked and the file is uploaded, the parser attempts auto-detection from column count and header names (8 columns → Dividends; 7 → Purchases; 6 → Sales). If detection is ambiguous, the first assistant message asks the user to confirm — it includes three buttons (`Dividends / Purchases / Sales`) as the interactive response.

**Client-side parse only at this stage** — no session created yet. Parse result is held in memory. "Parse file →" sends the normalized row matrix to `POST /api/portfolio/import/sessions` which creates the session and returns `import_session_id`.

---

## 5. Parse Summary Message

After parsing, the assistant posts its first message. The prose is LLM-generated (contextual framing); the card data is parser-derived.

```
─── Assistant ───────────────────────────────────────────────────────

  I've read your file. Here's what I found:

  ┌─── Parse summary ───────────────────────────────────────────────┐
  │  Type:      Purchases (7 columns detected)                      │
  │  Rows:      160 data rows                                       │
  │  Companies: 10 unique names                                     │
  │                                                                 │
  │  ✅  3 companies auto-resolved (saved alias)      → 72 rows     │
  │  💡  5 companies have suggestions (need confirm)  → 68 rows     │
  │  🔴  2 companies unresolved (need mapping)        → 20 rows     │
  │                                                                 │
  │  ⚠️  12 rows with data warnings (non-blocking)                  │
  │  🔴   3 rows with parse errors (blocking)                       │
  │                                                                 │
  │  Progress: 3/10 companies resolved · 72/160 rows ready          │
  └─────────────────────────────────────────────────────────────────┘

  I'll start with the companies that affect the most rows. For each
  one I'll only ask you once — your answer applies to every matching
  row in this file.

─────────────────────────────────────────────────────────────────────
```

---

## 6. Question Cards — Types and Behaviors

The assistant groups questions by priority and posts them in batches. Each ambiguity type has a dedicated interactive card. The user never types free text to answer these — they interact with structured UI controls embedded in the chat thread.

### 6.1 Company Mapping Card

One card per unique normalized company name. Posted in descending order of affected-row count.

```
─── Assistant ───────────────────────────────────────────────────────

  Let me resolve these 5 companies (68 rows).

  ┌─── Company: "ENAGAS SA"  ·  7 rows ────────────────────────────┐
  │  🔍 No match found in your security catalog.                   │
  │                                                                 │
  │  [ 🔍 Map existing ]                                            │
  │  [ ➕ Create security ]                                          │
  │  [ ✕ Exclude group ]                                            │
  └─────────────────────────────────────────────────────────────────┘

  ┌─── Company: "Coca-Cola"  ·  12 rows ───────────────────────────┐
  │  💡 Suggested: XNYS:KO — The Coca-Cola Company | NYSE | USD    │
  │     Confidence: 94% · "Name match + confirmed US ticker"       │
  │                                                                 │
  │  [ ✓ Confirm XNYS:KO ]   [ 🔍 Map existing ]                   │
  │  [ ✕ Exclude group ]                                            │
  └─────────────────────────────────────────────────────────────────┘

  ┌─── Company: "Coca-Cola FEMSA"  ·  3 rows ──────────────────────┐
  │  💡 Suggested: XNYS:KOF — Coca-Cola FEMSA SAB | NYSE | USD     │
  │     Confidence: 89% · "Subsidiary name match"                  │
  │  ⚠️  Different company from "Coca-Cola" above — kept separate  │
  │                                                                 │
  │  [ ✓ Confirm XNYS:KOF ]  [ 🔍 Map existing ]                   │
  │  [ ✕ Exclude group ]                                            │
  └─────────────────────────────────────────────────────────────────┘

─────────────────────────────────────────────────────────────────────
```

**Deduplication rule:** company names are grouped by `company_raw_normalized` (trim + collapse whitespace + uppercase). One mapping question per group. "Coca-Cola" and "Coca Cola" are the **same group** (normalized to `COCA-COLA`). "Coca-Cola" and "Coca-Cola FEMSA" are **separate groups** (different normalized strings). The assistant explicitly notes when two similar names are kept separate, so the user can decide if they should be merged.

**Explicit merge:** if the user types "merge Coca-Cola FEMSA with Coca-Cola", the assistant surfaces a confirmation card: "Map both 'Coca-Cola' and 'Coca-Cola FEMSA' to XNYS:KO? This means 15 rows all go to the same security." The user confirms or cancels. Merging is not automatic — it always requires explicit user action.

**Share classes always stay separate:** "BRK A" and "BRK B" are never auto-merged. A note appears if two names appear to be the same company's share classes (by similar prefix).

### 6.2 Map Existing Card (triggered by "Map existing" button)

The card expands to reveal the search combobox inline — no navigation away:

```
  ┌─── Company: "ENAGAS SA"  ·  7 rows ────────────────────────────┐
  │  🔍 Search: [ enagas                          ] ×              │
  │  ──────────────────────────────────────────────────────────── │
  │  ○  XMAD:ENG  Enagás S.A.      BME/XMAD  EUR                  │
  │  ○  XNYS:GAS  US Gas ETF       NYSE      USD                   │
  │  ──────────────────────────────────────────────────────────── │
  │  Not found?  [ ➕ Create security for "Enagás S.A." ]          │
  │                                                                 │
  │  [ Cancel ]                                                     │
  └─────────────────────────────────────────────────────────────────┘
```

Selecting a result collapses the card to `✅ Mapped: XMAD:ENG — Enagás S.A. | BME/XMAD | EUR  [Change]` and the assistant acknowledges with a one-line update: "ENAGAS SA → XMAD:ENG ✓  (7 rows unlocked)".

### 6.3 Create Security Inline

Triggered by the **"Create security"** button on a company mapping card, or by "Create security for X" at the bottom of the Map Existing search results when no match is found.

The form opens as a card within the chat thread — no navigation away from the import session.

**Prefill behavior:** the assistant pre-populates fields from any AI suggestion already computed for this company and from normalized company name heuristics. Pre-filled values are editable drafts — the user must confirm them by clicking "Create & map →". Nothing is auto-accepted.

```
  ┌─── Create security for "ENAGAS SA" ────────────────────────────┐
  │  ✏️  Fields pre-filled from suggestion — review and confirm     │
  │  ─────────────────────────────────────────────────────────────  │
  │  Ticker *         [ ENG          ]                              │
  │  Exchange / MIC * [ BME — Bolsa de Madrid (XMAD)         ▾ ]  │
  │  Currency *       [ EUR ▾ ]  (auto-suggested from XMAD)        │
  │  Display name *   [ Enagás S.A.                           ]    │
  │  ── Optional ─────────────────────────────────────────────────  │
  │  ISIN             [ ES0130960018                           ]    │
  │  IBKR conid       [                                        ]    │
  │  Other provider   [                                        ]    │
  │                                                                 │
  │  [ Cancel ]                       [ Create & map → ]           │
  └─────────────────────────────────────────────────────────────────┘
```

**Exchanges supported** (same list as `rusty-unified-securities-mapping-ux.md` §4):  
NYSE/XNYS · NASDAQ/XNAS · BME/XMAD · AMS/XAMS · LSE/XLON · SWX/XSWX · Manual MIC

**On success ("Create & map →"):**
- The new security is saved to the unified catalog immediately.
- The company group is marked `✅ Created` — every row sharing the same normalized company name is now resolved.
- Progress counts update instantly: `companies_resolved++`, `ready_rows += (row count for this group)`.
- **No file re-upload is required.** The parser re-runs validation only for the affected rows; all other session state is preserved.
- The card collapses to: `✅ Created: XMAD:ENG — Enagás S.A. | BME/XMAD | EUR  [Change]`
- The assistant posts a one-line acknowledgement: "ENAGAS SA → new security XMAD:ENG ✓  (7 rows unlocked)"
- The alias `ENAGAS SA (normalized) → XMAD:ENG` is saved globally for future imports.

**On validation failure (field-level):**
- The card **stays open**; the draft is not discarded.
- Only the failing field is highlighted in red with a specific inline message beneath it.
- Example — Ticker: `"ENG already exists on XMAD. Use Map existing to select it, or choose a different ticker."`
- Example — ISIN: `"Invalid format — expected ES + 10 alphanumeric characters."`
- The user corrects the highlighted field and retries immediately.

**On MIC + ticker collision (security already in catalog):**
- A banner appears at the top of the card: `"⚠️ XMAD:ENG already exists — Enagás S.A."` with two inline actions:
  - `[ Use XMAD:ENG ]` — maps the company group to the existing security (same result as Map existing)
  - `[ Change ticker ]` — clears the Ticker field, keeps all other draft values
- The draft is not discarded in either case.

**Catalog membership — what "created" means and what it does not:**

| Property | Behaviour on creation |
|---|---|
| Appears in Securities catalog (`/symbols`) | ✅ Immediately — with `—` in options-tracking columns |
| Available as mapping target in future imports | ✅ Immediately |
| Options-agent tracking (`watch_calls`, `watch_puts`, `pause_until_earnings`) | ❌ All OFF by default — user must enable explicitly |
| DGI score, momentum, entry tag, agent enrichment | ❌ Not populated — requires agent tracking to be enabled |
| `total_shares` / Holdings | ❌ 0 until import is confirmed and ledger replay runs |
| Ownership implied | ❌ No — the security record is neutral metadata; ownership derives from committed ledger movements only |
| Old routes (`/symbols`, `/symbols/:ticker`, `/economics`) | ✅ Unchanged — unified catalog is a data-layer extension, not a route change |

The user enables options-agent tracking from the Security detail page (`/symbols/:ticker`) after import — it is a separate deliberate action.

### 6.4 Batch Currency Card

Posted once if the source currency is not unambiguous from the file format (only for dividend imports; purchases and sales are always EUR):

```
  ┌─── Source currency ─────────────────────────────────────────────┐
  │  All amount columns in this file use one currency.              │
  │  What currency are these amounts in?                            │
  │                                                                 │
  │  [ EUR ]  [ USD ]  [ GBP ]  [ CHF ]                           │
  └─────────────────────────────────────────────────────────────────┘
```

Asked at most once. Answer applies to all rows. For purchases and sales (always EUR), this card is never shown.

### 6.5 Classification Card (Rights / Corporate Action)

Posted after company mapping is complete, grouped by security. One card per affected security, not per row:

```
  ┌─── 4 zero-cost rows — XMAD:REP (Repsol) ──────────────────────┐
  │  4 purchase rows show zero price, zero total, and zero         │
  │  commission with shares > 0. These may be shares received      │
  │  as part of a scrip/rights dividend.                           │
  │                                                                 │
  │  Dates:  15/03/2021 · 15/03/2022 · 15/09/2022 · 15/03/2023   │
  │  Shares: 9 · 9 · 10 · 11                                      │
  │                                                                 │
  │  How should I handle these?                                     │
  │                                                                 │
  │  [ Import as Corporate action pending ]                         │
  │    Rows enter the reconciliation queue to be completed later.   │
  │                                                                 │
  │  [ Defer — decide after import ]                                │
  │    Same as above; just reminds you in the queue.                │
  │                                                                 │
  │  [ Exclude these 4 rows ]                                       │
  └─────────────────────────────────────────────────────────────────┘
```

One confirmed answer applies to all 4 rows — not asked per row.

### 6.6 Row-Specific Exception Card

For warnings affecting a single row that cannot be resolved generically (e.g., arithmetic mismatch on one specific row):

```
  ┌─── Row 47 — XMAD:IBE · 15/06/2022  ────────────────────────────┐
  │  ⚠️  Arithmetic mismatch: Price × Shares ≠ Total               │
  │  Price: 8.123456    Shares: 12.500000                           │
  │  Price × Shares = 101.54   Total = 101.85   Δ = 0.31 EUR       │
  │                                                                  │
  │  [ Import with this discrepancy ]                                │
  │    Stores the file values as-is; discrepancy noted in record.   │
  │                                                                  │
  │  [ Exclude this row ]                                            │
  └──────────────────────────────────────────────────────────────────┘
```

Row-specific cards are posted after all company-level questions are resolved, so they do not interrupt the company-mapping flow.

### 6.7 Possible Top-Up Suggestion Card (Purchases only)

```
  ┌─── Possible top-up — XMAD:REP  ────────────────────────────────┐
  │  Row 23 (BUY · 18/03/2021 · €4.95 · commission only?)          │
  │  is within 30 days of a zero-cost share receipt (row 19).       │
  │  This may be a cash top-up for the scrip event.                 │
  │                                                                  │
  │  [ Link as cash top-up to the scrip event ]                     │
  │    Imports as CASH_TOP_UP leg; no separate BUY.                  │
  │                                                                  │
  │  [ Import as a regular BUY ]                                     │
  │    Default; you can reclassify later from Movements.            │
  │                                                                  │
  │  [ Defer this decision ]                                         │
  │    Imports as BUY with advisory flag.                            │
  └─────────────────────────────────────────────────────────────────┘
```

---

## 7. Question Prioritization and Progress

### 7.1 Ordering Rule

Questions are dispatched in this order:

1. **Company mapping** — posted all together in one batch, ordered by descending row count (highest-impact first)
2. **Batch currency** — if needed for dividends
3. **Classification choices** — per-security group, ordered by row count
4. **Row-specific exceptions** — individual non-blocking warnings requiring acknowledgement, grouped by warning type

The assistant never posts a classification question before all company questions are answered — the security identity must be established first.

### 7.2 Progress Indicator (Persistent)

A sticky progress bar appears above the chat input at all times during the resolve phase:

```
┌────────────────────────────────────────────────────────────────────┐
│  Import in progress —  8/10 companies resolved ·  142/160 rows ready
│  [████████░░]  89%   2 blockers remain                             │
└────────────────────────────────────────────────────────────────────┘
```

"X/Y companies resolved" counts `✅` (auto + confirmed + created). "X/Y rows ready" counts rows whose company is resolved and which have no remaining blocking issues.

The progress bar also appears inline as a short chip when the assistant posts a batch of questions: "Progress: 8/10 companies resolved · 142/160 rows ready."

### 7.3 Bulk Actions

After posting all company mapping cards in one batch, a footer below the batch shows:

```
[ ✓ Confirm all suggestions ≥ 85% confidence ({N} companies) ]
[ ✕ Exclude all unresolved companies ({M} companies, {K} rows) ]
```

These are the same bulk actions as in the wizard's Company Resolution screen, now surfaced as chat-level action buttons below the batch.

---

## 8. Account Assignment — Not Asked By Default

Account is **never raised as a question** during the import chat unless:
- The user types something like "assign to ING" or "this is from Fidelity"
- The user selects an account in the initial Upload card (if the card includes an optional account field in a future enhancement)

If account is assigned during the session (via user free-text or a command), the assistant acknowledges: "Got it — I'll assign all rows to 'ING Principal'. You can change this per-row in Movements after import."

If no account is assigned, the final confirmation card notes: "Account: (unassigned — assign after import from Movements)." No warning is produced.

---

## 9. Deferred Warnings and Commitments

Some warnings can be deferred — the row imports with an incomplete status that enters the reconciliation queue. The user is not required to resolve these during the import chat.

| Warning type | Default answer | Can defer? | Queue status after import |
|---|---|---|---|
| `corporate_action_pending` | "Import as pending" | Yes — no question needed | `pending_event_link` |
| `rights_pending` (dividends) | "Import as pending" | Yes | `pending_legs` |
| `possible_top_up` | "Import as BUY" | Yes — shown as optional card | Advisory flag on BUY |
| `arithmetic_mismatch` | Row stays checked; user can ignore | Yes | `arithmetic_mismatch` flag on record |
| `negative_inventory` (sales) | Row imports; warning stored | Yes — no question needed | `negative_inventory` flag |
| `commission_exceeds_proceeds` | Requires explicit acknowledgement | Acknowledge = defer | Flag on SELL record |

The assistant groups all deferrable warnings and asks once per group:

```
─── Assistant ───────────────────────────────────────────────────────

  These rows will import with flags for later review — no action
  needed now unless you want to exclude them:

  ┌─── Deferrable warnings ─────────────────────────────────────────┐
  │  🔵  8 Corporate action pending (zero-cost share receipts)      │
  │      → Enter reconciliation queue after import                  │
  │                                                                 │
  │  ⚠️   3 Arithmetic mismatch (price × shares ≠ total, < €0.50)  │
  │      → Import with mismatch flag; review in Movements           │
  │                                                                 │
  │  ⚠️   2 Negative inventory (sales before purchases recorded)    │
  │      → Import; warning clears when purchases are imported       │
  │                                                                 │
  │  [ Import all with flags ]  [ Let me review each ]             │
  └─────────────────────────────────────────────────────────────────┘
─────────────────────────────────────────────────────────────────────
```

"Import all with flags" skips individual review. "Let me review each" posts individual cards for each warning group.

---

## 10. Session Persistence and Revision

### 10.1 Session Save

The import session (`import_session_id`) is persisted server-side:
- All parsed rows with their normalized values
- All answers given (company mappings, currency, classification choices)
- Current progress state

If the user closes the tab and returns to `/portfolio/import`, an in-progress session banner appears:

```
┌─────────────────────────────────────────────────────────────────┐
│  📂 You have an unfinished import session from {date/time}       │
│  Type: Purchases · 142/160 rows ready · 2 blockers              │
│  [ Resume import ]   [ Start a new import ]                     │
└─────────────────────────────────────────────────────────────────┘
```

### 10.2 Revising a Prior Answer

Every answered card in the chat thread shows a `[Change]` link in its collapsed state. Clicking it re-expands the card to its interactive state. The user changes the answer; the session is revalidated for all rows affected by the change.

The assistant posts a revalidation message:

```
─── Assistant ───────────────────────────────────────────────────────

  Updated: "Coca-Cola" is now mapped to XMAD:KOF instead of XNYS:KO.
  Revalidating 12 rows…

  ┌─── Revalidation result ─────────────────────────────────────────┐
  │  12 rows updated to use XMAD:KOF | BME/XMAD | EUR             │
  │  No new warnings introduced.                                    │
  │  Progress: 8/10 companies resolved · 142/160 rows ready        │
  └─────────────────────────────────────────────────────────────────┘
─────────────────────────────────────────────────────────────────────
```

Revalidation is synchronous (< 500ms for typical batch sizes) because it re-runs the deterministic parser against already-normalized rows — no external calls needed if the security is already in the catalog.

### 10.3 User Free-Text Commands

The chat input accepts free-text, but the import assistant responds to a limited command vocabulary for speed:

| User input | Effect |
|---|---|
| `map ENAGAS to ENG` | Resolves "ENAGAS SA" → catalog search for "ENG"; confirms if found |
| `create ENG on BME EUR as Enagás S.A.` | Creates security inline; maps company |
| `exclude ENAGAS` | Excludes all rows for that company |
| `assign to ING` | Sets account for entire batch |
| `defer all warnings` | Applies default defer choice to all deferrable warnings |
| `show errors` | Scrolls to or expands the blocking rows section |

Free-text that does not match a command receives a natural-language response from the LLM plus a suggestion of the available interactive action.

---

## 11. Final Confirmation Message

When progress reaches 100% (all blockers resolved or excluded), the assistant posts the deterministic final card and enables the Confirm Import button:

```
─── Assistant ───────────────────────────────────────────────────────

  Everything's in order. Here's the final summary before I commit
  the import.

  ┌─── Import preview — ready to commit ───────────────────────────┐
  │  Type:     Purchases   Account: (unassigned)   Currency: EUR   │
  │                                                                 │
  │  ✅  138  BUY rows — will create ledger_txn: BUY               │
  │  🟡    2  Staged as top-up — ca_leg: CASH_TOP_UP               │
  │  🔵    8  Corporate action pending — reconciliation queue       │
  │  ⚠️    4  Warning rows — imported with flags                    │
  │  ✕    8  Excluded (3 by you · 5 company unresolved)            │
  │  ─────────────────────────────────────────────────────────────  │
  │  Total to import: 152 rows (of 160 parsed)                     │
  │                                                                 │
  │  Duplicate check: 0 matches.                                    │
  │                                                                 │
  │  ⚠️  8 rows will enter the reconciliation queue (2026 default). │
  │                                                                 │
  │  [ 📥 Download unresolved/errors report ]                       │
  │                                                                 │
  │  ┌─────────────────────────────────────────────────────────┐   │
  │  │  ✅  Confirm Import — 152 rows                          │   │
  │  │  No ledger writes have occurred yet.                    │   │
  │  └─────────────────────────────────────────────────────────┘   │
  └─────────────────────────────────────────────────────────────────┘

─────────────────────────────────────────────────────────────────────
```

**The "Confirm Import" button is the only action that triggers ledger writes.** The session can be abandoned at this point with zero side effects.

**"Download unresolved/errors report"** produces a downloadable file containing:
- Row indices and raw source values for excluded rows
- Warning codes and descriptions for flagged rows
- No financial values from committed records — only status metadata

---

## 12. Post-Import Message

After "Confirm Import" is clicked and the server commits:

```
─── Assistant ───────────────────────────────────────────────────────

  Done! 152 rows imported successfully.

  ┌─── Import complete ─────────────────────────────────────────────┐
  │  ✅ 152 movements recorded                                      │
  │  🔄 Ledger replay: 5 securities replayed.                       │
  │     XMAD:REP — 2 inventory warnings cleared (prior sales)      │
  │     XMAD:IBE — No change                                       │
  │     [+ 3 more]  [Show all]                                     │
  │                                                                 │
  │  🔵 8 rows in reconciliation queue (year 2026)                  │
  │                                                                 │
  │  [ View imported movements → ]                                  │
  │  [ Open reconciliation queue (2026) → ]                        │
  │  [ Assign account to all rows → ]                               │
  └─────────────────────────────────────────────────────────────────┘

  The reconciliation queue is a separate task — take your time.
  This import session is now archived.

─────────────────────────────────────────────────────────────────────
```

The chat session transitions to **read-only** after import. The thread is preserved in an import history (accessible via a list at `/portfolio/import/history`) but no further interactions are possible. A new import starts a fresh session.

**Reconciliation remains a separate context.** The reconciliation queue at `/portfolio/movements?status=pending_reconciliation&year=2026` is an independent workflow — it has its own chat task interface (task queue, not this import session). Linking the two from the post-import message provides navigation, not continuity of the same session.

---

## 13. Import Session State Machine

```
INITIAL            — no file uploaded
PARSING            — file received; parser running (async, < 2s for typical files)
RESOLVING          — parse complete; questions being answered
READY              — all blockers cleared; Confirm button enabled
CONFIRMING         — user clicked Confirm; ledger writes in progress
COMMITTED          — writes complete; session read-only
ABANDONED          — user navigated away without confirming (session preserved 7 days)
```

State is stored server-side as part of the `import_session` document. State transitions are triggered by parser events and user interactions, not by LLM responses. The LLM reads current state from the session to determine what to say next.

---

## 14. Accessibility

### Chat Thread

- The chat thread uses `role="log"` with `aria-live="polite"` so new assistant messages are announced without interrupting the user.
- Each assistant message is a `<div role="article">` within the log.
- LLM prose and cards are both within the same article, with cards identifiable by `role="region"` and a visible heading.

### Interactive Cards

- Company mapping cards: each action button has a full `aria-label` ("Confirm XNYS:KO — The Coca-Cola Company for company name Coca-Cola, 12 rows").
- Inline search combobox: `role="combobox"`, `aria-autocomplete="list"`, `aria-expanded`, `aria-controls` pointing to results `role="listbox"`.
- Progress bar: `role="progressbar"`, `aria-valuenow`, `aria-valuemin="0"`, `aria-valuemax="10"` (companies), and a text alternative: "8 of 10 companies resolved".
- "Confirm Import" button: `aria-describedby` pointing to a summary of what will happen — reads "152 rows will be imported. 8 will enter the reconciliation queue. No ledger writes have occurred yet."

### Keyboard Navigation

- Cards in the chat thread are focusable in document order.
- Within a card, Tab moves between action buttons; Enter/Space activates.
- The inline Create Security form traps focus within the card while open; Escape cancels and returns focus to the card's first button.
- The progress indicator is focusable and announces its current state on focus.

### Bulk Actions

- "Confirm all suggestions ≥ 85%": `aria-label="Confirm {N} company suggestions with confidence 85% or higher"`. After activation: `aria-live="assertive"` announces "{N} companies confirmed".
- "Defer all warnings": `aria-label="Defer all {N} deferrable warnings and import with flags"`.

---

## 15. API Surface — Chat Import Session

All parser, warning, and domain endpoints from the three import design documents and the unified securities design are unchanged. This section adds the session orchestration surface.

```
POST   /api/portfolio/import/sessions
       Body: { type: 'dividends' | 'purchases' | 'sales', rows: NormalizedRow[],
               raw_filename: string }
       Response: { session_id, state: ImportSessionState, summary: ParseSummary }
       Creates the session; runs server-side normalization and initial resolution.

GET    /api/portfolio/import/sessions/:id
       Response: ImportSessionState (full session state for resume)

PATCH  /api/portfolio/import/sessions/:id/answer
       Body: ImportAnswer (see §16)
       Response: { updated_state: ImportSessionState }
       Records one user answer; re-runs validation for affected rows.
       Idempotent: re-submitting the same answer is a no-op.

POST   /api/portfolio/import/sessions/:id/confirm
       No body. Triggers ledger writes for all non-excluded rows.
       Returns: { committed, failed, replay_results, session_archived: true }
       Idempotent per session_id: second call returns cached result.

GET    /api/portfolio/import/sessions/:id/report
       Returns: { excluded_rows: ExcludedRowReport[], flagged_rows: FlaggedRowReport[] }
       Report contains only row indices, warning codes, and status — no financial values.

GET    /api/portfolio/import/history
       Returns: { sessions: ImportSessionSummary[] } — list of past sessions.

POST   /api/portfolio/import/sessions/:id/chat
       Body: { user_message: string }
       Response: { llm_response: string, state_delta?: ImportSessionStateDelta }
       Routes free-text commands; LLM response is orchestration prose only.
       State changes are applied server-side; llm_response describes them.
```

---

## 16. TypeScript DTO Outlines — Session Layer

```typescript
// ─── Session state ────────────────────────────────────────────────────────────

type ImportSessionPhase =
  | 'INITIAL' | 'PARSING' | 'RESOLVING' | 'READY'
  | 'CONFIRMING' | 'COMMITTED' | 'ABANDONED';

interface ImportSessionState {
  session_id: string;
  import_type: 'dividends' | 'purchases' | 'sales';
  phase: ImportSessionPhase;
  created_at: string;
  updated_at: string;

  // Progress
  total_rows: number;
  ready_rows: number;                  // blocking issues resolved
  excluded_rows: number;
  companies_total: number;
  companies_resolved: number;

  // Question queue (what the assistant should ask next)
  pending_questions: ImportQuestion[];

  // Answered (for thread reconstruction on resume)
  answered: ImportAnswer[];

  // Summary
  row_counts: {
    buy: number; sell: number; dividend: number;
    corporate_action_pending: number; staged_top_up: number;
    warning: number; blocking: number; excluded: number;
  };

  account_id?: string | null;          // optional; null = unassigned
  source_currency?: SupportedCurrency;
  batch_id: string;                    // UUID; stable across session lifetime
}

// ─── Question types ────────────────────────────────────────────────────────────

type ImportQuestionKind =
  | 'company_mapping'         // one per unique company_raw_normalized
  | 'batch_currency'          // at most once per session
  | 'classification_group'    // per security, for zero-cost / rights rows
  | 'row_exception'           // per individual blocking/warning row
  | 'top_up_suggestion'       // per proximity-flagged BUY row
  | 'deferred_warnings_batch'; // one card covering all deferrable warnings

interface ImportQuestion {
  question_id: string;
  kind: ImportQuestionKind;
  priority: number;            // lower = asked sooner
  affected_row_count: number;
  // Kind-specific payload
  company_raw?: string;
  company_normalized?: string;
  suggestion?: CandidateSecurityDTO;
  row_index?: number;
  warning_code?: string;
  security_id?: string;        // for classification/top-up questions
  deferred_warnings?: { code: string; count: number }[];
}

// ─── Answer types ─────────────────────────────────────────────────────────────

type ImportAnswerKind =
  | 'company_confirmed'        // user confirmed a suggestion
  | 'company_mapped'           // user selected from search
  | 'company_created'          // user created a new security
  | 'company_excluded'         // user excluded all rows for this company
  | 'company_merged'           // user explicitly merged two normalized groups
  | 'currency_selected'
  | 'classification_chosen'    // corporate_action_pending | exclude | defer
  | 'row_acknowledged'         // user acknowledged a row-specific warning
  | 'row_excluded'
  | 'top_up_linked'            // BUY row linked as CASH_TOP_UP
  | 'top_up_as_buy'            // kept as BUY
  | 'warnings_deferred'        // bulk defer of all deferrable warnings
  | 'account_assigned';

interface ImportAnswer {
  question_id: string;
  kind: ImportAnswerKind;
  answered_at: string;
  // Payload varies by kind:
  security_id?: string;        // for company_confirmed / company_mapped / company_created
  ticker?: string;             // for display
  currency?: SupportedCurrency;
  classification?: 'corporate_action_pending' | 'exclude' | 'defer';
  account_id?: string;
  merged_into_normalized?: string; // for company_merged
}
```

---

## 17. Compatibility with Wizard Components

All interactive card components described in §6 are built from the same React components designed for the wizard UI:

| Card | Reused wizard component |
|---|---|
| Company mapping card | Company Resolution screen card (§2 of `rusty-unified-securities-mapping-ux.md`) |
| Search combobox | Security search combobox (§2.5 of same) |
| Create Security form | Create Security inline panel (§4 of same) |
| Classification card | Reconciliation queue action pattern |
| Row-exception card | Manual movement form error display pattern |
| Progress bar | New shared component; exposed as `<ImportProgress>` |
| Preview totals card | Step 3 summary box (all three import docs) |

The wizard navigation shell (step indicators, Back/Next buttons, breadcrumb) is **not rendered** in the chat experience. It may be retained in code as a fallback for accessibility-mode or for future embedded uses, but it is not the primary UX.

---

## 18. What This Design Does Not Change

- All parser rules (delimiter detection, Spanish locale, precision) — **unchanged**
- All warning taxonomy codes and severities — **unchanged**
- All domain model entities (`ca_event`, `ca_leg`, `ledger_txn`) — **unchanged**
- All API endpoints for import execution, dedup check, alias persistence, security creation — **unchanged**
- Reconciliation queue design, year filter, detail forms — **unchanged**
- Unified security catalog design, `security_id = MIC:ticker` — **unchanged**
- Account-optional, no-warning policy — **unchanged**
- Old routes (`/symbols`, `/symbols/:ticker`, `/economics`, options detail pages) — **unchanged**; unified catalog is a data-layer extension, not a route change; no redirects required

---

## Summary

**The multi-step wizard navigation is superseded by a chat thread** at the same `/portfolio/import` route. The chat experience embeds deterministic structured cards for every interactive decision — LLM text provides only contextual framing and transitions; all counts, amounts, and statuses come from server-side parser state.

**Session lifecycle:** upload → parse summary card → iterative question batches → final preview card → Confirm Import (only action that writes to the ledger) → post-import summary with ledger replay results → session archived.

**One question per reusable ambiguity:** companies are grouped by normalized key; one mapping answer covers all rows in the group. Similar names (different normalized strings) and share classes stay separate unless the user explicitly merges. Exact aliases auto-resolve silently; AI suggestions (with ticker, exchange, currency, confidence, reason) require one-click confirmation; bulk "Confirm all ≥ 85%" is the speed path.

**Question priority:** company mapping (highest-impact first) → batch currency → classification groups (per-security) → row exceptions. Progress chip always visible: "8/10 companies resolved · 142/160 rows ready."

**Account never asked** unless user initiates. Deferrable warnings (corporate action, rights, top-up, mismatch, inventory) can be batch-deferred in one card. Revision of any prior answer triggers revalidation. Session persists across browser close/reopen for 7 days.

**Reconciliation is a separate context** — post-import link navigates to the task queue at `/portfolio/movements?status=pending_reconciliation&year=2026`. The import session is archived and read-only after commit.

**Three-action company resolution:** each unresolved company group offers exactly "Map existing" (inline search), "Create security" (inline form), "Exclude group". The label and action set is consistent across all company cards. "Create security" opens an inline form pre-filled from the assistant's AI suggestion (editable, not auto-accepted). On success, all rows in the group resolve immediately and progress counts update — no re-upload required. On validation failure or MIC+ticker collision, the draft is preserved and only the offending field is highlighted with a precise correction message. The created security belongs to the unified Securities catalog shared by Symbols/watchlists and Portfolio; options-agent tracking flags default to OFF; `total_shares` = 0 until ledger replay; old routes (`/symbols`, `/symbols/:ticker`, `/economics`) are unchanged.

**Phase:** same as Phase 1d (unified securities gate); the chat shell replaces the wizard shell at that point. The wizard component library remains as the source of card implementations.
