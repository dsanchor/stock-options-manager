# Chat-Based Import — Agent Architecture

**Author:** Danny (Lead/Architecture)  
**Date:** 2026-09-05  
**Status:** PROPOSED — authoritative superseding recommendation  
**Supersedes:**
- `danny-dividend-csv-import-consolidated.md` §5 (4-step wizard UX)
- `rusty-dividend-import-ux.md` (wizard shell)
- `rusty-purchase-import-ux.md` (wizard shell)
- `rusty-sales-import-ux.md` (wizard shell)
- `rusty-unified-securities-mapping-ux.md` §2 (company resolution as wizard step)
- `danny-unified-security-master.md` §4.3 (wizard Step 2 — security resolution)

**Preserves unchanged:**
- All parsing, validation, dedup, warning taxonomy, column mappings, container/partition strategy, ca_event/ca_leg model, security master design, FX conventions, withholding semantics, null-vs-zero rules
- `danny-unified-security-master.md` §1–3, §5–12 (security identity, orthogonal states, migration)
- `danny-dividend-csv-import-consolidated.md` §1 RC-1 through RC-9 (all resolved conflicts except wizard UX)

**Directive:** `copilot-directive-20260905T172036+0200.md` — import via conversational chat; group reusable questions; ask each once.  
**Amendment:** `copilot-directive-20260905T172200+0200.md` — inline security creation during import chat; one canonical catalog; compatibility adapters.

---

## 1. Why Chat Replaces the Wizard

The prior wizard design (Type → Upload → Metadata → Company Resolution → Preview → Confirm) forced a rigid sequence. Real import sessions are iterative:

- The user pastes data and wants the system to figure out what it is.
- Company names repeat across files — the same question should never be asked twice across sessions.
- Some ambiguities (arithmetic mismatch on row 47) matter less than others (batch currency affecting all rows).
- The user may want to commit clean rows immediately while deferring ambiguous ones.
- A wizard provides no memory across imports — each upload is a fresh start.

Chat preserves all the prior design's validation rigor while allowing natural-language interaction, persistent cross-session memory, and adaptive question ordering.

**What changes:** The wizard shell (step indicators, Back/Next buttons, page-per-step layout) is replaced by a chat interface. Structured UI cards, tables, and inline forms appear INSIDE chat messages.

**What does NOT change:** Parsing rules, validation logic, dedup layers, warning taxonomy, column mappings, security resolution rules, commit semantics, container strategy — everything in the prior designs that is not about the wizard shell.

---

## 2. Fundamental Safety Invariant

> **The LLM never parses financial amounts, never computes arithmetic, never writes to the ledger directly, and never auto-confirms fuzzy security identity.**

The LLM is an **orchestration layer** that reads structured output from deterministic tools and presents it to the user as natural-language questions with structured cards. All source-of-truth operations are performed by deterministic code:

| Operation | Performed by | LLM role |
|-----------|-------------|----------|
| File parsing (delimiter, locale, encoding) | Deterministic parser | Reports result to user |
| Number parsing (Spanish comma/period) | Deterministic parser | Never touches raw numbers |
| Date parsing (DD/MM/YYYY) | Deterministic parser | Never interprets dates |
| Arithmetic validation (gross − WHT = net) | Deterministic validator | Reports mismatches |
| Dedup (row hash, cross-batch fingerprint) | Deterministic engine | Presents matches for user decision |
| Security alias resolution (exact match) | Deterministic lookup | Reports auto-resolved; no question generated |
| Security suggestion (fuzzy/AI) | LLM proposes from DB results | **Requires explicit user confirmation** |
| Import type detection | Deterministic column-shape match | May confirm with user if ambiguous |
| Row classification (rights, zero-cost, etc.) | Deterministic rules | Reports classifications |
| EUR amount computation | Deterministic: `amount_txn × fx_rate` | Never computes amounts |
| Ledger write (commit) | Deterministic API call | Only after explicit user "go" |

---

## 3. Session Model — Resumable, Idempotent, Auditable

### 3.1 Import Session Document

Each import conversation creates an `import_session` document in the `portfolio` container, partition `_global`:

```jsonc
{
  "id": "impsess_{session_id}",
  "doc_type": "import_session",
  "account_id": "_global",
  "session_id": "{uuid}",
  "status": "PARSING" | "RESOLVING" | "READY_TO_COMMIT" | "COMMITTED" | "ABANDONED",
  "import_type": "DIVIDEND" | "PURCHASE" | "SALE" | null,
  "created_at": "...",
  "updated_at": "...",

  "batch_metadata": {
    "source_currency": "EUR" | null,
    "fx_behavior": "AMOUNTS_ARE_EUR" | null,
    "batch_fx_rate": null,
    "account_id": null,     // null = _unassigned; never asked unprompted
    "batch_captures_dest_wht": null
  },

  "parse_result": {
    "file_name": "dividendos.csv",
    "file_sha256": "...",
    "detected_type": "DIVIDEND",
    "total_rows": 143,
    "rows": [ /* NormalizedImportRow[] */ ],
    "parse_warnings": []
  },

  "resolution_state": {
    "companies": {
      "APPLE INC": { "status": "AUTO_RESOLVED", "security_id": "XNYS:AAPL", "alias_matched": "APPLE INC", "row_count": 47 },
      "COCA COLA": { "status": "AI_SUGGESTED", "suggestions": [{"security_id":"XNYS:KO","confidence":0.94}], "row_count": 8 },
      "NUEVA EMPRESA": { "status": "UNRESOLVED", "row_count": 2 }
    },
    "row_decisions": {}  // row_index → user override
  },

  "pending_questions": [ /* PendingQuestion[], ordered */ ],
  "answered_questions": [ /* with timestamp, user response, rows_affected */ ],

  "commit_result": null,
  // Set after successful commit:
  // { "import_batch_id": "...", "rows_committed": 138, "rows_warning": 22, "rows_skipped": 5 }

  "conversation_turns": [
    // Audit log of every turn — not the chat UI history, but the tool-call record
  ]
}
```

### 3.2 No Hidden Writes — The Core Safety Rule

**Zero ledger writes (movements, ca_events, ca_legs) occur until the user explicitly confirms the commit.** During the entire Q&A phase, the only database writes are:

1. The `import_session` document itself (updated after each conversational turn).
2. `security` documents created via "Create security" (user-initiated via explicit chat action).
3. Alias additions to existing `security.aliases[]` (when user confirms a mapping).

Items 2 and 3 are visible, user-initiated, and independently useful (they improve future imports). They are NOT ledger writes.

### 3.3 Resumability

If the user closes the browser mid-conversation:
- The `import_session` document persists in Cosmos with all answered questions and resolution state.
- On return, the chat loads from the session document and presents: "Welcome back! You were importing {file_name}. {N} questions remain. Shall I continue?"
- All prior answers are preserved. No re-upload needed (parsed rows are stored in the session).
- Sessions older than 7 days with status ≠ COMMITTED are auto-marked ABANDONED (configurable).

### 3.4 Idempotency

The commit uses `session_id + source_row_number + normalized_row_hash` as the idempotency key. Re-committing is a no-op for already-written rows. Partial commit failure (network error mid-batch) can be retried safely.

---

## 4. Pending Questions — Structured Queue

### 4.1 Three-Level Hierarchy

| Level | Scope | Example | Impact |
|-------|-------|---------|--------|
| **Batch** | Entire file | "What currency are these amounts in?" | All rows |
| **Company** | All rows sharing one `empresa_raw` | "Is 'COCA COLA' the NYSE stock KO?" | 8–50 rows |
| **Row** | Single row | "Row 47: amounts don't add up (€0.50 off). Import anyway?" | 1 row |

### 4.2 Question Types

Each question is a typed record. The LLM renders it as natural language + structured card. The underlying data is always deterministic:

**Batch-level questions:**

| Type | Blocking? | Trigger | Default if unanswered |
|------|-----------|---------|----------------------|
| `BATCH_IMPORT_TYPE` | Yes (if ambiguous) | Column count doesn't match exactly one known format | Auto-detected if unambiguous |
| `BATCH_SOURCE_CURRENCY` | Yes | File has no currency column | `EUR` pre-suggested for this user |
| `BATCH_FX_BEHAVIOR` | Yes (if non-EUR) | Source currency ≠ EUR | Auto-set to `AMOUNTS_ARE_EUR` for EUR |
| `BATCH_FX_RATE` | Yes (if manual FX) | `fx_behavior = MANUAL` | — |

**NOT asked at batch level (by design):**
- `BATCH_ACCOUNT` — account is NEVER asked unless the user volunteers it. Default: `_unassigned`. This preserves the prior design's "no warning for missing account" rule.
- `BATCH_DEST_WHT_CAPTURED` — defaults to `null` (conservative). Asked only if user brings it up.

**Company-level questions:**

| Type | Blocking? | Trigger |
|------|-----------|---------|
| `COMPANY_CONFIRM_AI_SUGGESTION` | Yes | Fuzzy match found, confidence < 100% |
| `COMPANY_RESOLVE_UNMATCHED` | Yes | No match or suggestion found |

**Row-level questions:**

| Type | Blocking? | Trigger |
|------|-----------|---------|
| `ROW_ARITHMETIC_MISMATCH` | No | Amounts don't reconcile within tolerance |
| `ROW_POSSIBLE_DUPLICATE` | No | Cross-batch hash match |
| `ROW_YEAR_DATE_MISMATCH` | No | Año ≠ year(date) |
| `ROW_WITHHOLDING_EXCEEDS_GROSS` | No | WHTs > gross |
| `ROW_AMBIGUOUS_NUMBER` | No | Parse heuristic uncertain |
| `ROW_ZERO_COST_PURCHASE` | No | Purchase with price=0, total=0, commission=0 |
| `ROW_INVENTORY_SHORTFALL` | No | Sale qty > known holdings at date |

### 4.3 Ordering Algorithm

```
Priority 1: BATCH blocking questions (in type-priority order)
Priority 2: COMPANY blocking questions (ordered by rows_affected DESC)
Priority 3: Non-blocking questions (ordered by rows_affected DESC, then level)
```

Within each priority tier, the LLM may group multiple questions into one conversational turn (see §4.4).

### 4.4 Grouping — Ask Each Reusable Question Once

**The key UX innovation:** Instead of asking per-row questions, the system groups by the ambiguity key:

**Company grouping:** All unresolved companies are presented in ONE card. The user can confirm/reject/create in one interaction. Each confirmed mapping applies to ALL rows with that company name.

**Warning grouping:** If 12 rows have `ROW_ARITHMETIC_MISMATCH` and all have delta < €0.10, the LLM may ask: "12 rows have small arithmetic mismatches (max €0.10). Import them all with a warning flag?" — one answer for 12 rows.

**Singleton row questions:** Unusual one-off issues (one row with WHT > gross) are asked individually but batched into a "miscellaneous issues" message at the end.

**Grouping algorithm:**
```
For each question_type in pending_questions:
  Group by (question_type, similarity_of_context)
  If group.size > 3 AND all are non-blocking:
    Present as a batch question: "N rows have {issue}. Accept all?"
  Else:
    Present individually with structured cards
```

### 4.5 Answer Application — Deterministic Propagation

When the user answers a company-level question, the system deterministically updates ALL rows affected:

```
User confirms: "COCA COLA" → XNYS:KO

System action (deterministic, not LLM):
  1. Update resolution_state.companies["COCA COLA"].status = "CONFIRMED"
  2. Update resolution_state.companies["COCA COLA"].security_id = "XNYS:KO"
  3. Save "COCA COLA" to XNYS:KO security's aliases[] (cross-session memory)
  4. Re-validate all 8 rows with this company (they may now pass validation)
  5. Recalculate pending_questions (some may be eliminated)
  6. Update import_session document
```

The LLM does NOT perform step 1–6. It calls `answer_question(question_id, answer)` and the deterministic engine handles propagation.

---

## 5. Import Type Detection

### 5.1 Deterministic Column-Shape Matching

| Columns | Header signature | Detected type |
|---------|-----------------|---------------|
| 8 | Contains ≥5 of: Año, Empresa, Fecha de cobro, Importe Bruto, Importe Neto, Importe en Derechos, Retención Origen, Retención Destino | `DIVIDEND` |
| 7 | Contains ≥5 of: Año, Empresa, Fecha compra, Valor compra, Acciones, Total (€), Comisión | `PURCHASE` |
| 6 | Contains ≥4 of: Año, Empresa, Fecha venta, Acciones, Comisión, Total Venta | `SALE` |

Header matching is case-insensitive, accent-insensitive. Positional fallback if headers absent.

### 5.2 Ambiguous Case

If column count matches multiple types or headers are absent, a `BATCH_IMPORT_TYPE` question is generated. The LLM asks:

```
Assistant: I found {N} columns. This could be:
  💰 Dividends (8 columns: Año, Empresa, Fecha de cobro, ...)
  🛒 Purchases (7 columns: Año, Empresa, Fecha compra, ...)
  📤 Sales (6 columns: Año, Empresa, Fecha venta, ...)

Which one is it?
```

### 5.3 User Can State Type Proactively

If the user says "I'm importing my purchase history" before pasting, the type is set and the parser uses the corresponding column map. No detection question needed.

---

## 6. LLM Tool Boundaries — Guardrails

### 6.1 Tools Available to the Import Chat Agent

**Read-only tools (no side effects):**

| Tool | Purpose |
|------|---------|
| `parse_file(raw_text, file_name)` | Deterministic parse → `ParseResult` |
| `validate_batch(session_id)` | Run all validation rules, generate/update pending questions |
| `check_duplicates(session_id)` | Cross-batch dedup check |
| `resolve_companies(session_id)` | Run alias lookup + fuzzy matching for all companies |
| `suggest_security(normalized_name, known_securities)` | Return ranked fuzzy suggestions with confidence |
| `get_session(session_id)` | Reload full session state |
| `preview_commit(session_id)` | Generate final row-by-row preview with all resolutions applied |
| `list_securities()` | All canonical securities for search/display |
| `search_securities(query)` | Filtered search over securities |

**Write-through-confirmation tools (require user action):**

| Tool | Effect | Confirmation required |
|------|--------|----------------------|
| `answer_question(question_id, answer)` | Updates session resolution state; propagates to affected rows | User's chat response or button click |
| `create_security(mic, ticker, name, currency, isin?)` | Creates `security` doc in `_global` | User provides data |
| `commit_import(session_id)` | Writes movements/ca_events/ca_legs to `portfolio` container | **Explicit user confirmation** |

### 6.2 Guardrails — What the LLM Must Never Do

| Prohibited action | Enforcement mechanism |
|-------------------|-----------------------|
| Parse or recompute financial amounts | `parse_file` returns structured numbers; LLM never sees raw numeric text to interpret |
| Write to ledger during Q&A | `commit_import` validates `status = READY_TO_COMMIT` (all blocking questions answered) |
| Auto-confirm fuzzy security mapping | `answer_question` for `COMPANY_CONFIRM_AI_SUGGESTION` requires `answered_by = "user"` in session log |
| Invent a ticker or exchange MIC | `create_security` validates MIC against a known ISO 10383 list; rejects unknown MICs |
| Invent a currency | `BATCH_SOURCE_CURRENCY` answer validated against `EUR|USD|GBP|CHF` enum |
| Skip blocking questions | `commit_import` checks: no PENDING question with `blocking: true` exists |
| Present amounts not from parser | All numbers in structured cards carry `source: "parser"` annotation; LLM instructions forbid generating monetary values |
| Modify committed rows | `import_session` with `status: COMMITTED` is immutable; edit/void goes through normal movement APIs |

### 6.3 Hallucination Prevention for Security Mapping

The `suggest_security` tool returns only securities that exist in the `_global` partition. The LLM cannot suggest a security that doesn't exist in the database.

Each suggestion includes `security_id`, `display_name`, `ticker`, `exchange_mic` — all from the database. The confidence score is computed by a deterministic fuzzy matcher (Levenshtein distance + token overlap), not by the LLM.

For genuinely new securities, the user must provide exchange MIC and ticker via the `create_security` flow. The LLM may suggest a probable exchange based on batch currency (EUR → XMAD, USD → XNYS) but labels it as a suggestion, and validation rejects any MIC not in the ISO 10383 list.

---

## 7. Conversation Flow — Reference Sessions

### 7.1 Happy Path (Dividend Import, EUR, Known Companies)

```
User: [drops dividendos.csv file]

Agent: I've parsed your file — looks like a dividend history.
  📊 143 rows · 8 columns · Detected: Dividends
  ✅ 12 companies found, 10 auto-resolved from saved aliases.

  I need to confirm 2 companies:
  ┌─────────────────────────────────────────────┐
  │ 🤖 "COCA COLA" (8 rows) → XNYS:KO (94%)   │
  │    [ ✓ Confirm ]  [ ✗ Not this ]            │
  │                                              │
  │ 🤖 "REPSOL OIL" (4 rows) → XMAD:REP (88%) │
  │    [ ✓ Confirm ]  [ ✗ Not this ]            │
  └─────────────────────────────────────────────┘

  All amounts are in EUR — correct?  [ ✓ Yes, EUR ]  [ Change currency ]

User: Yes, confirm all.

Agent: ✅ All companies resolved. Currency: EUR.
  Running validation...

  📋 Results:
  • 131 rows ready to import
  • 8 rows with rights amounts (⚠️ imported with warning; complete later)
  • 3 rows with small arithmetic mismatches (max €0.08)
  • 1 possible duplicate (same date + company + gross as existing record)

  The 3 mismatches are all under €0.10 — likely rounding.
  Import them with a warning flag?  [ ✓ Yes ]  [ Show details ]

User: Yes, import the mismatches. What about the duplicate?

Agent: The duplicate is:
  Row 89: XNYS:JNJ · 15/03/2024 · Gross €125.43
  Matches existing movement from a previous import.
  [ Skip this row ]  [ Import anyway (with duplicate flag) ]

User: Skip it.

Agent: Got it. Here's your final summary:
  ┌─────────────────────────────────────────────┐
  │  Ready to import 142 rows:                   │
  │  ✅ 131 clean                                │
  │  ⚠️   8 rights pending (reconcile later)     │
  │  ⚠️   3 arithmetic warnings (accepted)       │
  │  ⏭️   1 skipped (duplicate)                  │
  │                                              │
  │  Account: (unassigned — assign later)        │
  │  Currency: EUR                               │
  │                                              │
  │  [ ✅ Import 142 rows ]  [ Cancel ]          │
  └─────────────────────────────────────────────┘

User: Import.

Agent: ✅ Done! 142 movements created.
  • 8 need reconciliation (rights pending) — [View queue →]
  • View all imported movements — [Open Movements →]
```

### 7.2 Edge Case — Unknown Company, User Creates Security

```
Agent: ❌ "NUEVA EMPRESA S.L." (2 rows) — I couldn't find a match.
  [ + Create security ]  [ 🔍 Search existing ]  [ Skip 2 rows ]

User: It's a new Spanish company, ticker ENG on Madrid.

Agent: Creating security:
  Exchange: XMAD (Bolsa de Madrid)
  Ticker: ENG
  Name: Nueva Empresa S.L.
  Currency: EUR
  ISIN: (none)
  
  [ ✓ Create & map ]  [ Edit details ]

User: Create it.

Agent: ✅ Created XMAD:ENG. Saved "NUEVA EMPRESA S.L." as an alias.
  2 rows now resolved. Moving on...
```

### 7.3 Resumable Session

```
User: [returns after closing browser]

Agent: Welcome back! You were importing dividendos.csv (143 rows).
  ✅ 10 companies resolved, currency confirmed (EUR).
  ⏳ 2 companies still need mapping. Pick up where you left off?

  🤖 "WIDGETS INTL" (1 row) — no match found.
  [ + Create security ]  [ 🔍 Search ]  [ Skip row ]
```

---

## 8. Structured Cards Inside Chat

Chat messages contain structured UI components, not just text. These are the same components that would have been wizard steps, now embedded in chat bubbles:

### 8.1 Card Types

| Card type | When shown | Interactive? |
|-----------|-----------|-------------|
| **Parse summary** | After file parse | Read-only |
| **Company resolution table** | When companies need mapping | Buttons: Confirm / Reject / Create / Search |
| **Batch metadata form** | When batch-level question asked | Dropdowns: currency, FX behavior |
| **Row warning group** | When non-blocking warnings grouped | Buttons: Accept all / Show details / Skip |
| **Row detail expansion** | User asks "show details" for a warning | Read-only table with per-row breakdown |
| **Duplicate comparison** | When possible duplicate found | Side-by-side: new row vs existing; Skip / Import anyway |
| **Final preview summary** | Before commit | Read-only summary + Import button |
| **Create security form** | User creates new security | Inline form: MIC, ticker, name, currency, ISIN |

### 8.2 Card Rendering

Cards are described in the assistant message as structured JSON blocks. The frontend chat renderer interprets them as React components. The LLM generates the card type and references the session data — it does NOT generate the card content (amounts, row counts, etc.) from scratch. All data comes from the `import_session` document.

```jsonc
// Example card in assistant message
{
  "type": "company_resolution_card",
  "session_id": "...",
  "companies": [
    { "name": "COCA COLA", "status": "AI_SUGGESTED", "suggestion": "XNYS:KO", "confidence": 0.94, "rows": 8 },
    { "name": "NUEVA EMPRESA", "status": "UNRESOLVED", "rows": 2 }
  ]
}
```

The frontend renders this as the interactive card shown in §7.1. Button clicks map to `answer_question` tool calls.

---

## 9. Reconciliation with Prior Designs

### 9.1 What the Wizard Designs Got Right (Preserved)

| Prior design element | Preserved as |
|---------------------|-------------|
| Column mappings (8/7/6 columns) | Unchanged — deterministic parser |
| Spanish locale number parsing | Unchanged — parser tool |
| Warning taxonomy (RC-6) | Unchanged — maps to question types |
| 3-layer dedup (RC-4) | Unchanged — `check_duplicates` tool |
| Null vs zero WHT (RC-8) | Unchanged — batch metadata default |
| `_unassigned` partition (RC-3) | Unchanged — default, never asked |
| Security resolution gate (unified-security §4) | Unchanged — blocking questions in chat |
| Alias persistence (unified-security §4.4) | Preserved — on security document |
| ca_event/ca_leg model (scrip architecture) | Unchanged |
| Arithmetic tolerance (0.02 EUR) | Unchanged — validator tool |
| Rights pending = WARNING on all years (RC-2) | Unchanged |
| Import batch provenance document | Unchanged |

### 9.2 What the Wizard Designs Got Wrong (Replaced)

| Prior element | Problem | Chat replacement |
|--------------|---------|-----------------|
| Fixed step order | User must complete steps in sequence even when some are irrelevant | Questions ordered by impact; irrelevant ones never asked |
| Separate alias mapper modal | Context switch; loses flow | Inline company resolution card in chat |
| Preview table as a dedicated page | Renders ALL rows; overwhelming for 143+ rows | Summary card with drill-down; user asks for details |
| "Continue" button gating | Binary — all resolved or stuck | Incremental progress; user sees unblocked row count growing |
| No cross-session memory | Each import starts fresh | Aliases persist; returning to session resumes state |
| Type selection as first step | User must know the type before uploading | Auto-detected; confirmed only if ambiguous |
| Per-row warning interaction | 12 individual "accept?" clicks for similar warnings | Grouped by type: "12 rows have X. Accept all?" |

### 9.3 What's New (Chat-Only Features)

| Feature | Description |
|---------|-------------|
| Natural-language override | User can say "skip all rows before 2020" instead of clicking 40 checkboxes |
| Contextual help | User can ask "what does 'rights pending' mean?" mid-import |
| Partial commit discussion | "Can I import the clean rows now and deal with the warnings later?" → Agent explains options |
| Cross-file awareness | "I imported dividends yesterday. Now I'm importing purchases for the same companies." → Aliases already saved |
| Proactive suggestions | If user uploads 6 columns but column 6 header says "Total (€)", agent suggests: "This looks like a sales file" |

---

## 10. Account Handling — No Question Unless Asked

The prior design's rule is strengthened in chat:

> The agent NEVER asks "Which broker account should these go to?" unprompted. All imports default to `_unassigned`. If the user volunteers "These are from my Fidelity account," the agent sets `account_id` accordingly. If the user asks "Can I assign an account?", the agent explains the option and offers an account picker.

This avoids a question that:
- Has no correct default (the user may not know or care yet)
- Is never blocking (account is administrative)
- Would be asked for every import if made part of the flow

---

## 11. Rights / Corporate Action Reconciliation

Rights rows (`Importe en Derechos > 0`) are imported with `WARNING_RIGHTS_PENDING` (non-blocking). The chat agent:

1. Reports rights rows in the summary: "8 rows have rights amounts. They'll be imported with a warning badge for later reconciliation."
2. Does NOT ask the user to classify rights during import. Reconciliation is a separate workflow.
3. If the user asks "What should I do about the rights rows?", explains the reconciliation queue and that it can be done later.

This keeps the import conversation focused on getting data into the ledger, not on the complex rights/scrip classification workflow.

---

## 12. API Surface — Import Chat Endpoints

```
POST /api/portfolio/import/chat
  Body: { session_id: string | null, message: string, file?: base64 }
  Returns: { session_id, assistant_message, cards: Card[], session_status }
  
  If session_id is null, creates a new session.
  If file is attached, runs parse_file first.
  The LLM processes the message with access to tools (§6.1).
  Returns structured response with optional cards.

GET /api/portfolio/import/sessions
  Returns: { sessions: ImportSessionSummary[] }
  Lists recent import sessions with status for resume.

GET /api/portfolio/import/sessions/{session_id}
  Returns: Full ImportSession document (for resume/audit).

POST /api/portfolio/import/sessions/{session_id}/commit
  Explicit commit endpoint — can also be triggered via chat ("import").
  Validates all blocking questions answered.
  Writes to portfolio container.
  Returns: CommitResult.

DELETE /api/portfolio/import/sessions/{session_id}
  Marks session ABANDONED. No ledger changes (nothing was written).
```

The chat endpoint is the primary interface. The explicit commit endpoint exists as a guardrail — even if the LLM calls `commit_import`, the backend independently validates that all blocking questions are answered before writing.

---

## 13. Phase Placement

| Phase | Scope |
|-------|-------|
| **Phase 1 (MVP)** | Manual entry of movements via slide-over forms. Security master (S-SEC-1, S-SEC-2). |
| **Phase 1b (Chat Import)** | Chat-based CSV import for dividends, purchases, sales. Replaces wizard. Includes: deterministic parser, validation engine, company resolution (alias + fuzzy), structured cards, session persistence, commit flow. |
| Phase 2 | ECB FX auto-fetch, IBKR Flex Query, multi-format broker imports, reconciliation tool. |
| Phase 3 | UI rename, total_shares derivation, cost-basis methods. |
| Phase 4 | Fiscal export, tax reporting. |
| Phase 5 | Charts, analytics, Economics integration. |

### Phase 1b Vertical Slices (Updated for Chat)

| Slice | Title | Acceptance |
|-------|-------|------------|
| S-CHAT-1 | Deterministic parser (all 3 formats) | Parses 8/7/6-column files. Spanish locale. BOM handling. Type auto-detection. Returns `ParseResult`. |
| S-CHAT-2 | Validation engine | Row-level validation rules. Warning taxonomy. Arithmetic checks. Tolerance configurable. Generates `PendingQuestion[]`. |
| S-CHAT-3 | Company resolution engine | Exact alias match (auto-resolve). Fuzzy suggestion with confidence score. Returns `ResolutionState`. |
| S-CHAT-4 | Import session persistence | `import_session` document CRUD. Resumable. `answer_question` propagation. Status transitions. |
| S-CHAT-5 | Chat UI + structured cards | Chat interface at `/portfolio/import`. Card renderer for all card types (§8.1). Button-to-tool mapping. |
| S-CHAT-6 | LLM orchestration agent | System prompt with tool definitions. Guardrails (§6.2). Question grouping logic. Natural-language answer interpretation. |
| S-CHAT-7 | 3-layer dedup engine | Batch idempotency, intra-file duplicate, cross-batch fingerprint. |
| S-CHAT-8 | Commit pipeline | `commit_import` with blocking-question validation. Writes to `portfolio` container. Provenance. Idempotent. |
| S-CHAT-9 | Cross-session alias memory | Confirmed mappings saved to `security.aliases[]`. Auto-resolve on next import. |

---

## 14. Acceptance Criteria

### 14.1 Safety

- [ ] LLM never generates monetary amounts — all numbers in cards sourced from `ParseResult`
- [ ] LLM never writes to ledger — `commit_import` is the only write path, gated on all blocking questions answered
- [ ] Fuzzy security suggestions require explicit user confirmation (click or natural-language "yes")
- [ ] Exact alias matches auto-resolve without question
- [ ] Created securities validated: MIC in ISO 10383 list, ticker non-empty alphanumeric
- [ ] No partial hidden writes during Q&A — only session document and user-initiated security creates

### 14.2 Question Efficiency

- [ ] Company mapping asked once per unique company name, answer applied to all matching rows
- [ ] Batch currency asked once, applied to all rows
- [ ] Similar row-level warnings grouped ("12 rows have X — accept all?")
- [ ] Questions ordered by rows_affected descending within each priority tier
- [ ] Non-blocking questions can be skipped; import proceeds with warning flags

### 14.3 Resumability

- [ ] Closing browser and returning loads session from Cosmos
- [ ] All prior answers preserved
- [ ] No re-upload needed (parsed rows in session document)
- [ ] Sessions auto-abandoned after 7 days of inactivity

### 14.4 Import Correctness

- [ ] All column mappings, parsing rules, validation logic identical to prior wizard designs
- [ ] 3-layer dedup unchanged
- [ ] Warning taxonomy unchanged
- [ ] `_unassigned` default for account; never asked unprompted
- [ ] Rights pending = WARNING on all years
- [ ] Null vs zero WHT preserved
- [ ] Idempotent commit

### 14.5 Conversational UX

- [ ] Import type auto-detected from column shape; confirmed only if ambiguous
- [ ] User can state intent in natural language ("import my purchases") before uploading
- [ ] User can ask questions mid-flow ("what does rights pending mean?")
- [ ] Structured cards render inside chat messages (not separate pages)
- [ ] Final preview summary shown before commit with explicit confirm button
- [ ] Post-commit: links to Movements view and reconciliation queue

---

## 15. Corrections to Prior Designs

| Prior design | What changes | What stays |
|-------------|-------------|-----------|
| Wizard shell (all 3 import UX docs) | **Replaced** by chat interface | All validation logic, card designs repurposed as chat cards |
| `danny-dividend-csv-import-consolidated.md` §5 | §5 (wizard steps) **superseded** | §1–4, §6–12 unchanged |
| `danny-unified-security-master.md` §4.3 | Wizard Step 2 **superseded** by chat company resolution | §4.1–4.2 (resolution pipeline), §4.4–4.5 unchanged |
| `rusty-unified-securities-mapping-ux.md` | Company resolution wizard page **superseded** | Card design reused inside chat |
| Security alias map location | Already on `security.aliases[]` per unified-security design | Unchanged |

---

## 16. Open Decisions

| # | Question | Recommended default |
|---|----------|-------------------|
| U-CHAT-1 | Should the chat agent use the existing global chat infrastructure (`/api/chat`) or a dedicated import-specific agent? | Dedicated agent with import-specific system prompt and tool set. Reuse chat UI components but separate backend route. |
| U-CHAT-2 | Maximum file size for in-session storage (parsed rows in Cosmos doc)? | 1000 rows. Larger files: stream rows to a blob, reference from session doc. |
| U-CHAT-3 | Should grouped row-level warnings show a "show all N rows" expansion? | Yes — the card has a collapse/expand for row details. |
| U-CHAT-4 | Should the import chat be accessible from the existing Chat page or only from Portfolio > Import? | Both — Chat page gains an "Import" mode alongside Portfolio/Quick Analysis. Portfolio menu links to it. |

---

## 17. Amendment — Inline Security Creation & Automatic Row Propagation

**Superseding note (2026-09-05T17:27):** This section incorporates directive `copilot-directive-20260905T172200+0200.md`. It expands §6.1 `create_security` tool, §7.2 edge-case flow, and adds detailed creation/rollback/propagation semantics. Cross-references `danny-unified-security-master.md` §14 for the canonical record model.

### 17.1 `create_security` Tool — Expanded Specification

**Input:**

| Parameter | Required | Validation |
|-----------|----------|-----------|
| `ticker` | Yes | 1–10 chars, alphanumeric + `.` (for BRK.B). Must not be empty. |
| `exchange_mic` | Yes | Must exist in known ISO 10383 operating MIC list. LLM cannot invent MICs. |
| `listing_currency` | Yes | `EUR \| USD \| GBP \| CHF` |
| `display_name` | Yes | Non-empty string. Pre-filled from `empresa_raw`. |
| `isin` | No | If provided: 12-char ISO 6166 format validated. |
| `country_of_domicile` | No | ISO 3166-1 alpha-2 if provided. |
| `broker_ids` | No | Object with optional `fidelity_cusip`, `ibkr_conid`, `sedol`. Format-validated per type. |
| `asset_class` | No | Default: `equity`. Enum: `equity \| etf \| reit \| bond \| preferred`. |

**Pre-creation collision checks (run BEFORE confirmation card):**

| Check | Query | Result |
|-------|-------|--------|
| Exact `security_id` collision | `SELECT * FROM c WHERE c.doc_type='security' AND c.security_id = '{mic}:{ticker}'` | 🔴 BLOCK: "This security already exists. Map to it instead?" |
| Same ticker, different MIC | `SELECT * FROM c WHERE c.doc_type='security' AND c.ticker = '{ticker}' AND c.exchange_mic != '{mic}'` | ⚠️ WARN: "Ticker {T} exists on {other_MIC}. Same company?" |
| ISIN collision (if provided) | `SELECT * FROM c WHERE c.doc_type='security' AND c.isin = '{isin}'` | ⚠️ WARN: "ISIN already assigned to another security." |
| Alias collision | `SELECT * FROM c WHERE c.doc_type='security' AND ARRAY_CONTAINS(c.aliases, '{normalized_name}')` | ⚠️ WARN: "Name already an alias for existing security." |

All checks are deterministic Cosmos queries against `_global`. The LLM presents the results; it never decides on behalf of the user.

**On collision BLOCK:** The tool returns `{ created: false, collision: { existing_security_id, display_name } }`. The LLM offers "Map to existing" as the primary action.

**On collision WARNING:** The tool returns `{ created: false, warnings: [...] }`. The LLM shows the warning and asks the user to confirm or change fields.

**On success:** The tool creates the document and returns `{ created: true, security_id, doc }`.

**Output:**

```jsonc
{
  "created": true | false,
  "security_id": "XMAD:ENG" | null,
  "collision": null | { "existing_security_id": "...", "display_name": "..." },
  "warnings": [],
  "doc": { /* full security document if created */ }
}
```

### 17.2 Post-Creation Automatic Propagation (Deterministic)

When `create_security` returns `created: true`, the following steps execute deterministically (NOT by the LLM):

```
1. Add normalize(empresa_raw) to new security's aliases[]
   (ensures future imports auto-resolve)

2. Update import_session.resolution_state.companies[empresa_normalized]:
   status → "CREATED"
   security_id → new security_id

3. For every row in parse_result.rows where
   normalize(row.company_raw) == empresa_normalized:
     Set row.resolved_security_id = new security_id

4. Re-run validate_batch for affected rows:
   - ERROR_UNRESOLVED_SECURITY removed
   - Other validations re-checked (arithmetic, dedup, etc.)
   - pending_questions updated: COMPANY_RESOLVE_UNMATCHED for this
     company moved to ANSWERED

5. Update import_session document in Cosmos

6. Return updated session summary to LLM for presentation
```

The LLM then reports: "✅ Created {security_id}. {N} rows resolved. {remaining} companies still unresolved."

### 17.3 Failure & Rollback Behavior

| Failure scenario | Session state | Security state | Recovery |
|-----------------|---------------|---------------|----------|
| Cosmos write fails (step 1 — security creation) | Unchanged; company UNRESOLVED | No document created | Retry safe. Agent: "Creation failed. Try again?" |
| Security created but session update fails (step 5) | Stale; company still shows UNRESOLVED | Security exists with alias | On resume: `resolve_companies` re-runs → alias match → auto-resolves. No data loss. No duplicate security. |
| Security created but browser closes before LLM responds | Session may not reflect creation | Security exists | On resume: same alias-based auto-resolution. |
| Collision detected (step before creation) | Unchanged | No document created | Agent shows collision; offers Map or Edit. |
| User cancels confirmation card | Unchanged | No document created | Agent moves to next question or offers retry. |

**Key safety property:** Security creation and session state update are independent operations. The system converges to the correct state on resume regardless of which operation completes, because the alias match on the security document is the authoritative link — if the security exists with the right alias, the next `resolve_companies` call will find it.

### 17.4 Conversation Card — Create Security

The confirmation card rendered in chat:

```
┌─────────────────────────────────────────────────┐
│  Create Security                                 │
│                                                  │
│  Ticker:        [ENG        ]                    │
│  Exchange:      [XMAD ▾ Bolsa de Madrid]         │
│  Currency:      [EUR ▾]                          │
│  Display name:  [Enagás S.A.        ]            │
│  ISIN:          [(optional)          ]           │
│                                                  │
│  security_id:   XMAD:ENG  (auto-computed)        │
│                                                  │
│  ✅ No collisions found.                          │
│  → Will resolve 7 rows for "ENAGAS SA"           │
│  → Alias "ENAGAS SA" saved for future imports    │
│                                                  │
│  [ ✓ Create ]  [ ✏️ Edit ]  [ Cancel ]            │
└─────────────────────────────────────────────────┘
```

If collisions exist, the card shows them:

```
│  ⚠️ Ticker ENG also exists as XLON:ENG            │
│     (Engineering Group plc — different company)    │
│  [ ✓ Create anyway ]  [ Map to XLON:ENG ]         │
│  [ ✏️ Change ticker ]                              │
```

### 17.5 No Duplicate Records — One Canonical Catalog

**Cross-reference to `danny-unified-security-master.md` §14.1:**

Creating a security during import creates ONE document — the canonical `security` in `portfolio._global`. It does NOT create a `symbol_config` in the `symbols` container. The `symbol_config` is operational state for options tracking and is created only when the user enables agent tracking.

This means a security created during import:
- IS visible in `list_securities()` and `search_securities()` (chat tools)
- IS usable for portfolio movements, holdings derivation, and future imports
- IS NOT visible in the existing Symbols/Watchlist page (until the user adds it there, which creates a `symbol_config` and links via `legacy_symbol`)
- Has NO `total_shares`, NO `positions[]`, NO activity/alert/report documents (those belong to `symbol_config`)

This is correct behavior: a security's existence is independent of watchlist membership or ownership.

### 17.6 Updated Acceptance Criteria

Append to §14:

**14.6 Inline Security Creation**

- [ ] Unresolved company in chat triggers `COMPANY_RESOLVE_UNMATCHED` question with "Create security" option
- [ ] Create security card shows: ticker (required), exchange/MIC (required, dropdown), currency (required), display name (required, pre-filled), ISIN (optional)
- [ ] Pre-creation collision checks: exact security_id, same-ticker-other-MIC, ISIN, alias — all deterministic
- [ ] Collision BLOCKS creation and shows existing security with "Map instead" option
- [ ] User must explicitly confirm creation (button click or natural-language)
- [ ] Post-creation: alias saved → all matching rows updated → batch revalidated → questions updated → summary reported
- [ ] All post-creation steps are deterministic (not LLM)
- [ ] Creation failure preserves session unchanged; company remains UNRESOLVED; retry is safe
- [ ] Resume after creation+disconnect auto-resolves via alias match (no manual re-creation needed)
- [ ] Created security exists ONLY in `portfolio._global` — no `symbol_config` created
- [ ] Created security immediately usable for import resolution and future imports
- [ ] LLM cannot fabricate MIC values — validated against ISO 10383 known list
- [ ] LLM cannot fabricate ticker — user provides; alphanumeric validation only

---

*End of chat-based import architecture (with amendment §17). This replaces the wizard shell across all import types while preserving every parsing, validation, and ledger-write decision from prior designs. No production code.*
