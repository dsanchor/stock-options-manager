# Conversational Import — Synthetic Test Matrix & Acceptance Criteria

**Author:** Basher (Tester/Reviewer)  
**Date:** 2026-09-05  
**Directives:** `copilot-directive-20260905T172036+0200.md`, `copilot-directive-20260905T172200+0200.md`  
**Depends on:** `basher-purchase-csv-validation.md`, `basher-sales-csv-validation.md`, `livingston-security-identity-import-matching.md`, `rusty-unified-securities-mapping-ux.md`  
**Constraint:** All fixtures are fully synthetic. No real user financial rows reproduced.

---

## Model contract

The chat importer wraps the deterministic import pipeline in a conversational question-and-answer loop. The pipeline's arithmetic, deduplication, idempotency, and ledger-write rules are **unchanged**; the chat layer adds:

1. **Grouping** — identical or equivalent ambiguities are asked once.
2. **Reuse** — a confirmed answer is applied to every row that shares its trigger.
3. **Ordering** — security resolution questions surface first (highest impact); batch-level questions next; row-specific acknowledgements last.
4. **Revision** — any answer may be corrected before final confirmation; correction triggers full revalidation.
5. **Gate** — no ledger write occurs before the user issues an explicit final confirmation.
6. **Create-in-flow** — when a company has no catalogue entry, the user may create the security from within the conversation without navigating away.

**Canonical identifier format (from Livingston):** `{ticker}:{MIC}` (e.g., `TCO:XNYS`).  
**Account** — optional, never asked by the conversational layer.  
**AI suggestions** — advisory only; never auto-applied.  
**Exact durable aliases** — auto-resolve silently; no question generated.

---

## Conversation flow

```
Upload → Parse & group → Q&A loop → Preview → Explicit confirm → Commit → Summary
```

Q&A ordering within the loop:
1. Security resolution questions (one per unique unresolved company group)
2. Batch-level questions (currency, date-format locale if ambiguous)
3. Row-specific WARN acknowledgements (commission > total, zero-cost, etc.)

State is persisted after every answered question. No DB writes until step 6.

---

## Test matrix

### A — Repeated exact normalized name: one question, one answer, all rows

*Normalization: lower-case, collapse whitespace, strip trailing punctuation.*

| TC | Scenario | Expected |
|----|----------|---------|
| A-01 | `SynthCo` appears in 10 rows (same normalization); not in catalogue; no alias | Exactly 1 security question generated for "SynthCo"; answer applied to all 10 rows |
| A-02 | A-01: user confirms `SynthCo → TCO:XNYS` | All 10 rows resolve to `TCO:XNYS`; no further security question for that name |
| A-03 | A-01: user confirms, then a 11th row with `SynthCo` is added (intra-session re-upload) | No new question; existing confirmed mapping applies automatically |
| A-04 | `SynthCo` (10 rows) + `WidgetCorp` (3 rows), both unknown | Exactly 2 security questions generated; each answered and applied independently |
| A-05 | `SynthCo` and `SynthCo ` (trailing space) after normalization are identical | Treated as one group; 1 question |
| A-06 | `SynthCo` and `SYNTHCO` after normalization are identical | Treated as one group; 1 question |

### B — Similar but distinct names/classes/exchanges not merged

| TC | Scenario | Expected |
|----|----------|---------|
| B-01 | `DualCo A` and `DualCo B` both unknown | 2 separate questions; answers stored under distinct group keys; never merged |
| B-02 | `SynthEx` appears with ticker hints `SYN:XNYS` (3 rows) and `SYN:XLON` (2 rows) | 2 separate questions (exchange differs); never merged even though base name is identical |
| B-03 | `Synth Beverages Inc` and `Synth Beverages Ltd` both unknown; AI scores each at 0.91 for same candidate | 2 separate questions; user must answer each independently |
| B-04 | `SynthCo` in purchase rows and `SynthCo` in sale rows; same normalized name | 1 question; confirmed `security_id` applies to both purchase and sale rows |
| B-05 | `DualCo A` confirmed in Batch 1 session; Batch 2 has `DualCo A` (exact alias) and `DualCo B` (no alias) | `DualCo A` auto-resolves (alias); 1 new question for `DualCo B` only |
| B-06 | `SynthCo` (10 rows) where 7 are 2024 purchases and 3 are 2023 dividends | 1 security question; same `security_id` applied to all 10; per-row type handled by pipeline |

### C — AI suggestions: never auto-confirmed

| TC | Scenario | Expected |
|----|----------|---------|
| C-01 | AI returns 1 candidate for `SynthCo` at score 0.91 | Candidate surfaced in conversation; commit button disabled until user explicitly confirms or rejects |
| C-02 | AI returns 1 candidate at score 0.99 (highest possible) | Same as C-01; score threshold never triggers auto-confirm |
| C-03 | AI returns 1 candidate; user reads message but does not respond | Question remains open; conversation does not advance to preview; commit impossible |
| C-04 | AI suggests `TCO:XNYS`; user ignores suggestion and types `TCO2:XNYS` manually | `TCO2:XNYS` is used; AI suggestion discarded; no blending of AI suggestion with user input |
| C-05 | AI suggests mapping for group of 10 rows; user confirms | All 10 rows resolved in one confirm action; 10 displayed in the confirmation receipt |
| C-06 | AI suggestion surfaced; user says "confirm" in free text | Confirmation must be via explicit UI control (button/checkbox), not free-text parsing; ambiguous free-text does not auto-apply |
| C-07 | AI returns no candidates (score < threshold) | No suggestion displayed; user must type or create security manually |

### D — Exact saved aliases auto-resolve without question

| TC | Scenario | Expected |
|----|----------|---------|
| D-01 | Alias `synth cola → TCO:XNYS` saved in catalogue; new batch contains `Synth Cola` | Auto-resolves; no question generated; resolution shown in pre-Q&A summary |
| D-02 | Alias `synth cola → TCO:XNYS` saved; new batch contains `Synth Cola Inc` (not exact after normalization) | Not auto-resolved; AI surfaces suggestion; question generated |
| D-03 | Alias created mid-session (user answers question in Session 1); Session 2 with same name | Auto-resolves in Session 2 (alias persisted to catalogue) |
| D-04 | Alias deleted from catalogue; next batch with the alias name | Question generated (no longer auto-resolving) |
| D-05 | Alias lookup is case-insensitive: `SYNTH COLA` matches alias `synth cola` | Auto-resolves; no question |
| D-06 | Two aliases point to different `security_id`s for different names; batch has both names | Both auto-resolve independently; 0 questions for those companies |

### E — Currency asked once per batch/group

| TC | Scenario | Expected |
|----|----------|---------|
| E-01 | All rows in CSV have no currency field; all amounts plausible as EUR | One currency confirmation question for the batch; answer applied to all rows |
| E-02 | CSV has rows that signal EUR (3) and GBP (4) by amount patterns or explicit column | Two currency questions (one per group); not per-row |
| E-03 | Currency explicitly declared in file header or metadata | No currency question asked |
| E-04 | User answers EUR for batch → realizes it should be GBP → revises (see Group H) | All rows revalidate with GBP |
| E-05 | All rows resolve to same currency from deterministic data (e.g. Total (€) column name) | No currency question asked; currency inferred from column name |

### F — Account never asked

| TC | Scenario | Expected |
|----|----------|---------|
| F-01 | CSV has no account column | No account question appears anywhere in the conversation |
| F-02 | CSV has an 8th `Cuenta` column with an unknown value | No account question; value silently defaults to primary account |
| F-03 | Conversation transcript for any batch | Grep for "account" / "cuenta" → must not appear as a question type |
| F-04 | CSV has `Cuenta = ""` (empty) | No account question; defaults to primary |

### G — Row-specific ambiguities remain separate from group questions

| TC | Scenario | Expected |
|----|----------|---------|
| G-01 | Year/date mismatch on row 3 only | Question scoped to row 3; shows row index and both values; not merged with any security question |
| G-02 | Commission > total on row 7 | Separate question for row 7; shows NC computed value; user acknowledges or rejects that row |
| G-03 | Zero-cost row on row 12 (`SynthCo`, VP=0, T=0, C=0) | Separate question: "Row 12 looks like a scrip/dividend share — classify as PENDING_CORPORATE_ACTION?" |
| G-04 | Two zero-cost rows: row 5 (`SynthCo`, 2024-01-10) and row 9 (`SynthCo`, 2024-06-15) | Two separate questions (different dates); not collapsed even for same company |
| G-05 | Year mismatch on rows 3 and 8, same mismatch pattern | Grouped into one question: "Rows 3 and 8 show year/date mismatch — were these intentional?" with both rows listed |
| G-06 | Security question for `SynthCo` (group) + date mismatch on one of its rows | Two questions in sequence; security question first; date mismatch question second; not merged |
| G-07 | Row-specific WARN (negative inventory after ledger replay) | Separate question per affected sale row; references the specific row and the computed position |

### H — User can revise an answer; rows revalidate

| TC | Scenario | Expected |
|----|----------|---------|
| H-01 | User confirms `SynthCo → TCO:XNYS`; later revises to `TCO2:XNYS` | All rows that used `TCO:XNYS` mapping revalidate with `TCO2:XNYS`; preview regenerates |
| H-02 | Revision mid-session, before preview shown | Revalidation triggered immediately; no stale preview presented |
| H-03 | Revision changes 2 BLOCK rows to ACCEPT | Preview updates; commit button becomes active if no BLOCK rows remain |
| H-04 | Revision changes 2 ACCEPT rows to BLOCK (user picks wrong exchange class, then corrects to a non-existent one) | Preview updates; commit button disabled; user informed of new BLOCKs |
| H-05 | User revises currency from EUR to GBP | All rows revalidate; derived values (NC = TV − C) unchanged (source values are currency-agnostic numerics); currency tag updated |
| H-06 | User answers Q3 (security), then revises Q1 (currency) | Full revalidation from Q1 onward; Q2 and Q3 answers preserved if still consistent; flagged for re-confirmation if affected |
| H-07 | User revises an answer that was used to create a durable alias | Alias is not persisted until explicit final confirmation; revision cancels any pending alias creation |

### I — Resume session preserves state

| TC | Scenario | Expected |
|----|----------|---------|
| I-01 | 3 of 5 questions answered; user closes browser tab | Reopen: same 3 answers loaded; conversation resumes at Q4; no re-parsing of file |
| I-02 | Session times out at question 3 | Reopen: same state; Q3 answer retained if submitted, else re-asked |
| I-03 | Answer to Q2 not yet given when session closes | Reopen: Q2 re-presented with same context |
| I-04 | All questions answered, preview shown, user closes without confirming | Reopen: preview re-shown; no ledger writes have occurred |
| I-05 | Session opened on a second device | Same conversation state presented; answers already given shown as read-only; new answers can be given |
| I-06 | Session committed successfully; user reopens via history | Summary view shown; conversation is read-only; no re-commit possible |
| I-07 | Session state preserved across revisions: user revises Q1, closes, reopens | Revised Q1 answer preserved; revalidation state preserved |

### J — Retry and commit idempotency

| TC | Scenario | Expected |
|----|----------|---------|
| J-01 | Commit succeeds; user clicks "Commit" again (double-click or UI race) | Idempotency key prevents duplicate rows; second commit is a no-op with same success summary |
| J-02 | Commit triggers; network error before DB ack; user retries | Same rows committed; idempotency keys ensure no duplication |
| J-03 | Commit succeeds; user re-uploads identical CSV in a new session | ❌ BLOCK all rows (cross-session idempotency key collision) |
| J-04 | Partial commit: rows 1–5 write successfully; row 6 fails mid-transaction | Full rollback per atomicity rule; 0 rows committed; retry commits all 6 |
| J-05 | Commit succeeds; alias creation succeeds; retry | Alias dedup: existing alias not duplicated in catalogue |
| J-06 | Q&A session completed; user types "import now" in chat | Must still require explicit UI confirmation (button); free-text "import" does not trigger commit |

### K — No ledger writes before explicit final confirmation

| TC | Scenario | Expected |
|----|----------|---------|
| K-01 | All questions answered; preview shown; user closes browser without clicking confirm | No ledger rows written; conversation state preserved for resume |
| K-02 | Session times out after final preview | No ledger rows written |
| K-03 | Confirm dialog shown; user clicks "Cancel" | No ledger rows written |
| K-04 | User says "yes import" in free text at preview stage | No write triggered; explicit UI confirm button still required |
| K-05 | Background session state saves (Q&A answers, preview result) | These are conversation-state writes only; ledger (`purchase_import_keys`, `sale_import_keys`, lot records) untouched |
| K-06 | Security alias creation (from create-in-flow) | Alias creation in the security catalogue is also deferred until final confirmation; a failed commit does not leave orphan aliases |

### L — Invalid LLM/tool output cannot alter deterministic values

| TC | Scenario | Expected |
|----|----------|---------|
| L-01 | LLM response suggests a corrected `Valor compra` (price) that differs from source CSV | Source CSV value used unchanged; LLM suggestion ignored; LLM output not written to any field |
| L-02 | LLM invents a `Total (€)` value in its response | Source value authoritative; invented value discarded |
| L-03 | LLM suggests `security_id = "TCO:XNYS"` in free-form response text without user confirmation | Mapping not applied; still BLOCK; user must confirm via explicit UI |
| L-04 | Tool returns JSON with a `security_id` not present in the security catalogue | Tool output rejected; row remains BLOCK; error logged |
| L-05 | LLM describes commission as "likely a data error" and suggests 0.00 | Source commission value unchanged; LLM commentary shown as context only |
| L-06 | LLM response modifies idempotency key fields in its output | Idempotency key recomputed from source CSV values only; LLM cannot influence it |
| L-07 | Tool returns a modified `Acciones` (quantity) value | Source quantity unchanged; ε-check uses source values only |
| L-08 | LLM output contains a `security_id` for a different row's company | Mapping applied only to rows matching the question's company group; cross-row contamination not possible |

### M — Partial import respects BLOCK vs WARN rows

| TC | Scenario | Expected |
|----|----------|---------|
| M-01 | After Q&A: 5 ACCEPT + 1 BLOCK + 1 WARN (acknowledged) | 5 + 1 = 6 rows imported; BLOCK row excluded; summary shows breakdown |
| M-02 | After Q&A: 5 ACCEPT + 0 BLOCK + 1 WARN (NOT acknowledged) | 5 rows imported; WARN row excluded; summary notes exclusion |
| M-03 | After Q&A: 0 ACCEPT + 1 BLOCK | Commit refused; "0 rows to import" shown; user directed to resolve BLOCK |
| M-04 | User types "skip the blocked row" in conversation | BLOCK cannot be skipped via conversation command; BLOCK row must be fixed or removed from file |
| M-05 | WARN row acknowledgement happens in conversation flow (user says "I understand, include it") | Acknowledgement recorded only if via explicit per-row UI control; free-text acknowledgement not sufficient |
| M-06 | All rows ACCEPT | All imported; no WARN/BLOCK items in summary |
| M-07 | BLOCK present; user fixes underlying issue (e.g., maps security in create-in-flow) | BLOCK lifted; row moves to ACCEPT; revalidation shows updated preview before commit |

### N — Final summary counts are exact

| TC | Scenario | Expected summary |
|----|----------|-----------------|
| N-01 | 10 rows: 8 ACCEPT + 1 BLOCK + 1 WARN (not acknowledged) | "8 imported, 1 excluded (WARN not acknowledged), 1 refused (BLOCK: unresolved security)" |
| N-02 | 0 imported (all blocked) | "0 rows imported. 5 blocked (unresolved security)." |
| N-03 | All 10 rows imported | "10 rows imported." |
| N-04 | Mixed file: 4 purchases + 3 sales + 2 dividends imported | "9 rows imported: 4 purchases, 3 sales, 2 dividends." |
| N-05 | Summary count must match actual DB row count | Post-commit DB query for session's idempotency keys must return count = reported imported count |
| N-06 | Multiple BLOCK reasons (2 rows: unresolved security; 1 row: invalid date) | Summary lists both BLOCK reasons with row counts: "2 blocked (unresolved security), 1 blocked (invalid date)" |
| N-07 | Durable aliases created during session | "N rows imported. M new security aliases saved." (aliases counted separately from row count) |

---

## Create-in-flow: security creation within conversation

*(Directive 2026-09-05T17:22:00+02:00)*

When the user's answer to a security question is "this company doesn't exist yet — create it":

| TC | Scenario | Expected |
|----|----------|---------|
| CF-01 | Company `NewCo Ltd` has no catalogue entry; user selects "Create new security" in conversation | Inline mini-form presented: ticker, MIC/exchange, canonical name; user fills in and submits |
| CF-02 | User fills CF-01 form with `NCL:XBRU`; submits | `NCL:XBRU` created in security catalogue; all rows with `NewCo Ltd` resolve; conversation advances |
| CF-03 | User fills CF-01 form with ticker that already exists in catalogue (`TCO:XNYS`) | Error: "TCO:XNYS already exists — did you mean to map to it?" with confirm/cancel option |
| CF-04 | User starts create-in-flow form; closes browser without submitting | No catalogue entry created (same gate as K-06); rows remain BLOCK |
| CF-05 | User creates security with invalid MIC code `ZZZZ` | Validation error in form; creation refused; rows remain BLOCK |
| CF-06 | Two different unresolved companies in same batch; user creates both in-flow | Two separate create-in-flow sessions; each creates its own catalogue entry; both groups resolve |
| CF-07 | User creates `NCL:XBRU` in-flow; session committed; new session with `NewCo Ltd` | Auto-resolves via the new catalogue entry (T1 exact match); no question |
| CF-08 | User creates security in-flow; revises the answer (H-01 pattern) before committing | Created security reverts to staging state; revision allowed before final confirmation |

---

## Compound conversation cases

| TC | Combination | Expected |
|----|-------------|---------|
| CC-01 | Batch with 2 unknown companies (both get questions) + 1 date mismatch row | 3 questions total: 2 security (first), 1 date mismatch (after); answers independent |
| CC-02 | 1 unknown company (10 rows) + user revises after Q&A complete + session close before confirm | Revised answer preserved; no ledger writes; resume shows revised preview |
| CC-03 | AI suggests mapping for company group; user confirms; one row in that group has year mismatch | Security question answered → year mismatch question surfaced for that row; security mapping already applied |
| CC-04 | Create-in-flow (CF-02) + network error during commit (J-02 pattern) | Rollback: 0 ledger rows committed; new catalogue entry also rolled back (or flagged pending); idempotent retry re-presents create confirmation |
| CC-05 | Exact alias (D-01) + same batch has a variant name (D-02) | Alias name auto-resolves (no question); variant name generates AI question; 2 different outcomes for 2 names |
| CC-06 | User answers security question (mapping A), then revises to mapping B, then revises back to A | Final answer is A; all rows use A; no intermediate state persists |
| CC-07 | 5-question session; user answers Q1–Q3; revises Q1; Q4 and Q5 remain unanswered | Q1 revision triggers revalidation; Q4 and Q5 still pending; cannot commit until Q4 and Q5 answered |

---

## Acceptance criteria

### Conversation Q&A phase

1. Each distinct unresolved company name (after normalization) generates exactly one question per session, regardless of row count.  
2. The question message displays: company name as it appears in CSV, row count and row indices, any AI suggestions with confidence scores (labelled "suggestion — requires confirmation").  
3. AI suggestions appear as selectable options, not as pre-selected answers; the confirm control is disabled until the user makes an explicit selection or provides a manual mapping.  
4. Free-text input in the conversation cannot trigger a commit, apply a mapping, or acknowledge a WARN.  
5. Exact durable alias matches are resolved before the Q&A phase begins; they appear in a pre-Q&A summary ("auto-resolved: N companies").  
6. Account is not mentioned in any question.  
7. Session state (all answered questions and their answers) is persisted after each answer; a reload presents the same state.  
8. Questions are ordered: security resolution → batch-level (currency, locale) → row-specific WARNs.  
9. Each question is independently answered; a revision to question N re-triggers revalidation of all rows affected by question N without discarding answers to other questions.

### Preview phase

1. Preview is not rendered until all security resolution questions are answered.  
2. Preview shows the deterministic pipeline result (ACCEPT / WARN / BLOCK per row) computed from source CSV values; no LLM-derived values appear in the row table.  
3. BLOCK rows are grouped by company/reason with row indices, matching the grouped-count format from `basher-purchase-csv-validation.md §GRP-*`.  
4. WARN rows require explicit per-row UI acknowledgement before commit activates for those rows.  
5. Revised answers re-render the preview; the previous preview is replaced, not appended.

### Commit phase

1. Commit requires an explicit final confirmation control (button/checkbox); free-text "confirm" does not suffice.  
2. Commit is refused if any BLOCK row remains.  
3. Commit is a single atomic DB transaction: all accepted rows and their idempotency keys write together, or none do.  
4. Durable aliases created in-flow are committed in the same transaction as the import rows; a failed commit does not persist orphan aliases.  
5. On success, the import session becomes read-only; no re-commit is possible.

### Summary phase

1. Summary counts are exact integers matching the DB row count verifiable by post-commit query.  
2. Summary distinguishes: rows imported, rows excluded (WARN not acknowledged, with reason), rows refused (BLOCK, with reason per group), aliases created.  
3. Summary for a mixed file (purchases + sales + dividends) breaks down by row type.  
4. "0 rows imported" is a valid, clearly communicated outcome, not an error state.

---

_No real user financial data is contained in this document. All values, company names, and dates are synthetic._
