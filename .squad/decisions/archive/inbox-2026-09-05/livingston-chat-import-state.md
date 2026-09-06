# Conversational Import — Session State-Machine Persistence

**Author:** Livingston (Persistence & Integration Engineer)  
**Date:** 2026-09-05  
**Revised:** 2026-09-05T17:29Z — per `copilot-directive-20260905T172200+0200.md`: adds `pending_creation` field to import_question schema and new §15 defining the session-side inline creation protocol. Cross-reference: `livingston-security-identity-import-matching.md §10` holds the full write sequence and collision checks; this document captures how the session state machine coordinates with that protocol.  
**Status:** DESIGN ONLY — no production edits.  
**Directives:** `copilot-directive-20260905T172036+0200.md`, `copilot-directive-20260905T172200+0200.md`  
**Depends on:** `livingston-security-identity-import-matching.md`, `livingston-dividend-csv-import.md`, `livingston-purchase-csv-import.md`, `livingston-sales-csv-import.md`

---

## 1. What This Design Covers

The three CSV importers (Dividends, Purchases, Sales) are delivered through a conversational flow:
the system parses the file, groups all ambiguities of the same kind, and asks the user one question per group in a structured sequence. Each answer that is reusable — company → security mapping, rights-amount classification, currency clarification — fans out deterministically to every affected row. The user never answers the same question twice for the same logical entity.

This document defines the persistence model and state machine that backs that conversation. It is orthogonal to the UI contract (owned by Rusty); it defines what the database stores, how state transitions work, and how the service mediates between the LLM and the deterministic ledger.

**Core rules enforced by this design:**
1. No ledger record (`ledger_txn`, `ca_event`, `ca_leg`) is written before the user explicitly confirms the commit.
2. The LLM only receives sanitized structural metadata — never financial amounts, quantities, or individual row data.
3. All state transitions and fan-out are owned by the deterministic service layer; the LLM produces proposals only.
4. One answer to a group question applies to every row sharing the exact group fingerprint — never to merely similar rows unless the user explicitly extends the mapping.
5. Changing an answer triggers a deterministic revalidation chain; the LLM is not re-called unless a brand-new entity name appears.
6. Account is optional and never asked by default; missing account defaults silently to `_unassigned`.

---

## 2. Container and Partition Recommendation

### 2.1 New Container: `import_sessions`

Import sessions are **not** stored in `portfolio_ledger`. Reasons:

| Concern | Portfolio Ledger (`/account_id`) | Import Sessions (proposed) |
|---------|----------------------------------|---------------------------|
| TTL | No TTL (permanent ledger) | 7-day TTL; must not risk deleting ledger records |
| Document size | Small, append-only records | Can reach ~500 KB for large files (many questions, candidates, history) |
| Write pattern | Append-mostly; infrequent updates | Frequent state-transition writes during a conversation |
| Partition semantics | `/account_id`; session has no stable account yet | Self-partitioned per session (uniform distribution) |
| Indexing needs | Heavy (account, security, date, type) | Light (state, created_by, expires_at only) |

**Container name:** `import_sessions`  
**Partition key:** `/session_id`  
**TTL policy:** Container default TTL = `-1` (no expiry); individual documents set `ttl` in seconds to control expiry.

This self-partitioning model (each session is its own logical partition) gives Cosmos uniform throughput distribution with no hot partitions even during batch imports. All documents belonging to one session (session doc + question docs + LLM call records) share the same `/session_id` partition value, enabling single-partition reads for the entire session context.

**Provisioning note:** `import_sessions` requires TTL to be enabled at the container level (`defaultTtl = -1`, meaning documents control their own TTL). See `scripts/provision_cosmosdb.sh`. The `portfolio_ledger` container must not have TTL enabled.

### 2.2 Document Types in `import_sessions`

| `doc_type` | Co-located with session partition? | Notes |
|------------|------------------------------------|-------|
| `import_session` | Yes (it IS the partition anchor) | One per import conversation |
| `import_question` | Yes (`session_id` is the partition key) | Separate documents when question count > 15 |
| `llm_call_record` | Yes | LLM provenance; expires with session |

---

## 3. State Machine

The import session moves through a linear series of named states. The service is the sole authority on state transitions — UI cannot set state directly. Transitions are triggered by events: file upload, answer submission, validation completion, commit confirmation.

```
                    ┌─ answer changed ─────────────────────────────┐
                    ▼                                               │
CREATED ──► FILE_PARSED ──► BATCH_Q ──► ENTITY_Q ──► GROUP_Q ──► ROW_Q ──► VALIDATING ──► PREVIEW_READY ──► COMMIT_CONFIRMED ──► COMMITTED
                                                                                                │                     ▲
                                                                                             (blocking               │
                                                                                            issue found) ────────────┘
                                                                                                          (user resolves)
Any state (before COMMITTED) ──► EXPIRED  (TTL elapsed)
```

### 3.1 State Definitions

| State | Description | Entry trigger | Normal exit |
|-------|-------------|---------------|-------------|
| `CREATED` | Session document written; file not yet parsed | File received by API | File parse completes |
| `FILE_PARSED` | Rows extracted; entity groups and ambiguity fingerprints computed; no questions asked | Parse service completes | All questions generated (no BATCH questions needed) or first BATCH question exists |
| `BATCH_QUESTIONS` | Asking file-level setup questions | First BATCH question exists | All BATCH questions answered |
| `ENTITY_QUESTIONS` | Asking company-to-security mapping questions, one per distinct normalized name | BATCH questions complete | All ENTITY questions answered |
| `ROW_GROUP_QUESTIONS` | Asking shared-ambiguity classification questions, one per fingerprint group | ENTITY questions complete | All ROW_GROUP questions answered or explicitly skipped |
| `ROW_QUESTIONS` | Asking per-row exception questions (rare) | ROW_GROUP question broken into rows by user | All per-row questions answered or explicitly skipped |
| `VALIDATING` | Service running deterministic validation pass | Any state transition or answer change | Validation completes (clean or with issues) |
| `PREVIEW_READY` | All questions answered, validation clean, preview snapshot computed | Validation clean | User clicks Confirm |
| `COMMIT_CONFIRMED` | User has confirmed; ledger writes in progress | Confirm button clicked | All records written |
| `COMMITTED` | Immutable; all movement IDs recorded | Last ledger write succeeds | Terminal — no further transitions |
| `EXPIRED` | TTL elapsed before COMMITTED; no ledger records written | CosmosDB TTL deletion triggers | Terminal |

**Regression:** When the user changes a previously-given answer, the state machine can regress. For example, changing an ENTITY answer while in `PREVIEW_READY` regresses to `ENTITY_QUESTIONS` (to re-examine questions that depend on the changed entity). The regression target is always the lowest state with outstanding questions.

---

## 4. Question Scopes

Every import_question document has a `scope` field that controls fan-out behavior.

### 4.1 BATCH Scope

Applies to the entire file. Must be answered before any ENTITY or GROUP question is generated, because BATCH answers may affect how rows are interpreted.

| `question_type` | When generated | Default if not asked |
|-----------------|----------------|----------------------|
| `IMPORT_TYPE` | Column headers are ambiguous or file has no header row | Inferred from first valid row shape |
| `SOURCE_CURRENCY` | File columns lack explicit EUR label; amount scale is ambiguous | EUR (all three CSV formats are EUR-denominated) |
| `SOURCE_MARKET` | Optional; user may provide exchange context to strengthen Stage 2 matching | Not asked; Stage 2 skipped |
| `ACCOUNT_ASSIGNMENT` | **Only asked if** `account_profiles` collection is non-empty AND user has opted in to be asked | `_unassigned` — silently, no question, no warning |

Account assignment is the only BATCH question that is never asked by default. If the user has not configured any `account_profile` documents, the account question is never generated. If account profiles exist, the user can be offered a dropdown within the import flow, but only if they have enabled this in import settings. The default is always `_unassigned` with no friction. This is an administrative convenience, not a data-quality concern.

### 4.2 ENTITY Scope

One question per distinct `empresa_normalized` value found in the file. Asks the user to map the free-text company name to a canonical `security_id`.

**Fan-out rule:** Answering this question resolves the security identity for every row sharing the exact same `empresa_normalized` value — no exceptions. The system cannot apply an ENTITY answer to a merely similar name (e.g., answering "Coca Cola Company" → KO:XNYS does not resolve "Coca-Cola Co." which is a different `empresa_normalized` key). Each normalized variant generates its own ENTITY question.

**Normalization is the authority for group membership.** Two rows share an ENTITY question if and only if their `empresa_normalized` values are byte-for-byte identical after applying the same normalization pipeline (NFKD Unicode, lowercase, collapse whitespace, strip common legal suffixes: `s.a.`, `plc`, `inc.`, `corp.`, `ltd.`, `n.v.`, `ag`, `sa`, `se`, `co.`).

ENTITY questions are ordered by `row_count DESC` within the session — the most-frequently-occurring company is asked first so the user feels immediate progress.

**Possible answer types for ENTITY questions:**

| `answer_type` | Description |
|---------------|-------------|
| `SELECTED_CANDIDATE` | User picked one of the presented candidates |
| `CREATED_NEW_SECURITY` | User created a new `security_master` via the sub-flow |
| `SKIPPED_COMPANY` | User chose to skip all rows for this company in this batch |

`SKIPPED_COMPANY` is batch-specific. Skipped rows are counted but not committed. The company name is not added to the alias map (the user has not confirmed an identity for it). A future import of the same file would surface the same company as unresolved.

### 4.3 ROW_GROUP Scope

After ENTITY questions are answered, rows for each resolved security are analyzed for shared anomalies. Rows that share an identical **ambiguity fingerprint** within the same `security_id` form a ROW_GROUP and receive a single shared question.

**Ambiguity fingerprint** is a deterministic string key computed from non-financial structural facts:
```
fingerprint = "{security_id}|{import_type}|{anomaly_type}|{key_structural_params}"
```

Examples:
| Fingerprint | Example anomaly | Generated question type |
|-------------|-----------------|------------------------|
| `KO:XNYS\|DIVIDEND\|RIGHTS_AMOUNT_PRESENT\|CASH_ALSO_PRESENT` | `Importe en Derechos > 0` and `Importe Bruto > 0` in same row | `RIGHTS_AMOUNT_CLASSIFICATION` |
| `ULVR:XLON\|PURCHASE\|ZERO_COST_POSITIVE_SHARES` | price=0, total=0, fee=0, shares>0 | `ZERO_COST_CLASSIFICATION` |
| `IBE:XMAD\|PURCHASE\|ARITHMETIC_DELTA_CONSISTENT_POSITIVE` | principal check fails with consistent ~1–3% positive delta across all rows | `PRICE_CURRENCY_CLARIFICATION` |
| `NESN:XSWX\|PURCHASE\|ARITHMETIC_DELTA_CONSISTENT_NEGATIVE` | consistent negative delta (commission may be included in Total) | `COMMISSION_IN_TOTAL_CLARIFICATION` |

**Fan-out rule:** Answering a ROW_GROUP question applies the chosen classification to every row sharing the exact fingerprint — no exceptions. If the user believes some rows in the group are different, they can click "Handle rows individually" which **breaks the group**: the ROW_GROUP question is marked `SUPERSEDED`, and individual ROW questions are generated for each member row.

**Non-blocking ROW_GROUP questions:** Not all ROW_GROUP questions block the commit. Examples:
- `YEAR_DATE_MISMATCH_CLASSIFICATION` (year field disagrees with date field): blocking — we cannot commit until we know the correct date.
- `RIGHTS_AMOUNT_CLASSIFICATION`: blocking — we cannot classify the leg type without this answer.
- `ZERO_COST_CLASSIFICATION`: blocking for cost-basis determination; however, the row can be imported as `PENDING_SCRIP_CLASSIFICATION` even without this answer if the user explicitly chooses "Import as pending — classify later." This is the only case where a blocking ROW_GROUP question can be bypassed by the user's explicit informed choice.

### 4.4 ROW Scope

Applied to individual rows that do not fit any group fingerprint — their anomaly is unique. Examples: a single negative-value row, a single row with an impossible date, a single duplicate candidate.

ROW questions are generated last and are ordered after all GROUP questions so the user handles the common cases first. Each ROW question blocks only the individual row; other rows are not affected. An unanswered ROW question at commit time causes the row to be excluded from the commit (counted in `rows_excluded_at_commit`).

The service minimizes ROW question generation. If two rows have the same anomaly type but slightly different key parameters (e.g., same arithmetic error direction but different magnitudes), the service checks whether a ROW_GROUP fingerprint can be constructed. It always prefers grouping over individual questions.

---

## 5. `import_session` Document Schema

```jsonc
{
  // ── Identity ──────────────────────────────────────────────────────────
  "id": "impsess_{ulid}",
  "session_id": "impsess_{ulid}",    // partition key; identical to id
  "doc_type": "import_session",
  "created_by": "dsanchor",
  "created_at": "2026-09-05T11:00:00Z",
  "updated_at": "2026-09-05T11:07:45Z",

  // ── File provenance ───────────────────────────────────────────────────
  "file": {
    "filename": "dividendos_historicos_2016_2025.xlsx",
    "content_hash_sha256": "e3b0c44298fc1c14...",  // hash of raw bytes; primary dedup key
    "byte_size": 28412,
    "uploaded_at": "2026-09-05T11:00:00Z",
    "detected_format": "XLSX",          // XLSX | TSV | CSV
    "detected_delimiter": null,         // null for XLSX; "\t" or "," for text formats
    "detected_encoding": "UTF-8",
    "store_raw_file": false,            // user opt-in; raw bytes discarded after parse
    "raw_file_doc_id": null            // populated if store_raw_file = true
  },

  // ── Import classification ─────────────────────────────────────────────
  "import_type": "DIVIDEND",           // DIVIDEND | PURCHASE | SALE | null (BATCH question)
  "inferred_import_type": "DIVIDEND",  // system inference from column header pattern
  "import_type_confidence": "HIGH",    // HIGH | MEDIUM | LOW | UNKNOWN
  "import_type_confirmed_by_user": true,

  // ── Account ───────────────────────────────────────────────────────────
  "account_id": "_unassigned",         // always present; default = "_unassigned"
  "account_asked": false,              // whether system generated an ACCOUNT_ASSIGNMENT question
  "account_confirmed_by": null,        // null when defaulted without asking

  // ── State machine ─────────────────────────────────────────────────────
  "state": "ENTITY_QUESTIONS",
  "state_history": [
    { "state": "CREATED",            "entered_at": "2026-09-05T11:00:00Z", "exited_at": "2026-09-05T11:00:04Z" },
    { "state": "FILE_PARSED",        "entered_at": "2026-09-05T11:00:04Z", "exited_at": "2026-09-05T11:00:05Z" },
    { "state": "BATCH_QUESTIONS",    "entered_at": "2026-09-05T11:00:05Z", "exited_at": "2026-09-05T11:01:30Z" },
    { "state": "ENTITY_QUESTIONS",   "entered_at": "2026-09-05T11:01:30Z" }
  ],
  "current_question_id": "q_impsess_abc_0004",

  // ── Parsed row summary ────────────────────────────────────────────────
  "rows_total": 85,
  "rows_parse_failed": 0,              // could not be parsed at all (bad locale, corrupt row)
  "rows_all_zero": 2,                  // all monetary amounts are zero; auto-excluded from commit
  "rows_blocked_awaiting_answers": 14, // associated with unanswered questions
  "rows_ready_to_commit": 62,          // all questions answered, validation clean
  "rows_skipped_by_user": 9,           // SKIPPED_COMPANY answers

  // ── Question inventory ────────────────────────────────────────────────
  // When questions_inline = true, full question objects are in questions[].
  // When questions_inline = false, question documents are separate (same partition).
  "questions_inline": true,
  "questions": [ /* see §6 */ ],
  "questions_total": 8,
  "questions_answered": 5,
  "questions_skipped": 0,
  "questions_remaining": 3,

  // ── Entity groups ─────────────────────────────────────────────────────
  // One entry per distinct empresa_normalized value in the file.
  "entity_groups": [
    {
      "empresa_raw_representative": "Coca Cola Company",  // first raw value seen (display only)
      "empresa_normalized": "coca cola",
      "raw_variants_seen": ["Coca Cola Company", "COCA COLA COMPANY"],  // all variants sharing this norm
      "row_count": 14,
      "row_numbers": [3, 7, 12, 18, 25, 31, 37, 43, 49, 55, 61, 67, 73, 79],
      "years_present": [2018, 2019, 2020, 2021],
      "pipeline_stages_run": ["STAGE_1", "STAGE_4", "STAGE_5"],
      "stage1_matched": false,
      "stage4_top_candidate_security_id": "KO:XNYS",
      "stage4_top_score": 0.87,
      "stage5_llm_call_id": "llmcall_xyz789",
      "stage5_ai_ran": true,
      "resolved_security_id": "KO:XNYS",  // null until ENTITY question is answered
      "question_id": "q_impsess_abc_0004",
      "resolution_status": "PENDING"       // PENDING | RESOLVED | SKIPPED
    }
    // ... one entry per distinct empresa_normalized
  ],

  // ── Row groups (ambiguity fingerprints) ───────────────────────────────
  "row_groups": [
    {
      "group_id": "rg_impsess_abc_001",
      "security_id": "ULVR:XLON",               // known only after ENTITY question answered
      "ambiguity_fingerprint": "ULVR:XLON|DIVIDEND|RIGHTS_AMOUNT_PRESENT|CASH_ALSO_PRESENT",
      "fingerprint_type": "RIGHTS_AMOUNT_CLASSIFICATION",
      "row_count": 6,
      "row_numbers": [22, 28, 35, 41, 47, 53],
      "prerequisite_entity_question_id": "q_impsess_abc_0005",
      "question_id": "q_impsess_abc_0007",
      "blocking_for_commit": true,
      "allow_import_as_pending": false,         // true only for ZERO_COST_CLASSIFICATION
      "resolved_classification": null,           // set when question is answered
      "broken_into_rows": false,
      "resolution_status": "PENDING"
    }
  ],

  // ── Validation ────────────────────────────────────────────────────────
  "validation_run_id": "valrun_impsess_abc_003",   // monotonic counter per session
  "validation_in_progress": false,
  "validation_clean": false,
  "validation_last_ran_at": "2026-09-05T11:05:00Z",
  "validation_warnings": [
    // Aggregate view; individual warnings are on staged row docs
    { "warning_code": "WARNING_YEAR_DATE_MISMATCH", "affected_row_count": 1, "question_id": "q_impsess_abc_0008", "blocking": true }
  ],
  "validation_blockers_count": 3,   // count of blocking warnings; commit is gated on 0

  // ── Preview snapshot ──────────────────────────────────────────────────
  // Populated only when state = PREVIEW_READY. Immutable until an answer change triggers revalidation.
  "preview_snapshot": null,          // see §7

  // ── Commit ────────────────────────────────────────────────────────────
  "commit_confirmed_at": null,
  "commit_confirmed_by": null,
  "committed_at": null,
  "committed_movement_ids": [],      // ledger_txn IDs written (append-only; survive partial failure)
  "commit_batch_id": null,           // import_batch doc ID in portfolio_ledger
  "rows_excluded_at_commit": 0,      // ROW-scope questions unanswered; row excluded not rejected
  "rows_committed": 0,

  // ── Lifecycle ─────────────────────────────────────────────────────────
  "ttl": 604800,                     // 7 days in seconds from created_at
  "expires_at": "2026-09-12T11:00:00Z",
  "resumed_count": 0,                // incremented each time user resumes after a gap
  "last_resumed_at": null,

  // ── Optimistic concurrency ────────────────────────────────────────────
  "_etag": "\"0x8D...\"",            // CosmosDB-managed; used for conditional writes
  "_ts": 1725526065                  // CosmosDB-managed timestamp
}
```

---

## 6. `import_question` Document Schema

```jsonc
{
  "id": "q_{session_id}_{seq:04d}",
  "session_id": "impsess_abc",       // partition key
  "doc_type": "import_question",

  // ── Classification ────────────────────────────────────────────────────
  "scope": "ENTITY",
  // Enum: BATCH | ENTITY | ROW_GROUP | ROW

  "question_type": "SECURITY_IDENTITY",
  // BATCH scope types:
  //   IMPORT_TYPE | SOURCE_CURRENCY | SOURCE_MARKET | ACCOUNT_ASSIGNMENT
  // ENTITY scope types:
  //   SECURITY_IDENTITY
  // ROW_GROUP scope types:
  //   RIGHTS_AMOUNT_CLASSIFICATION | ZERO_COST_CLASSIFICATION |
  //   PRICE_CURRENCY_CLARIFICATION | COMMISSION_IN_TOTAL_CLARIFICATION |
  //   ARITHMETIC_MISMATCH_CONFIRMATION | YEAR_DATE_MISMATCH_CLASSIFICATION |
  //   DUPLICATE_GROUP_CONFIRMATION
  // ROW scope types:
  //   NEGATIVE_VALUE_EXPLANATION | IMPOSSIBLE_DATE | SINGULAR_DUPLICATE |
  //   SINGULAR_ARITHMETIC_MISMATCH

  // ── Scope keys (exactly one set is populated per scope) ───────────────
  // ENTITY scope:
  "scope_entity_normalized": "coca cola",
  // ROW_GROUP scope:
  "scope_row_group_id": null,
  "scope_fingerprint": null,
  // ROW scope:
  "scope_row_number": null,

  // ── Sequencing ────────────────────────────────────────────────────────
  "sequence_number": 4,
  "prerequisite_question_ids": ["q_impsess_abc_0001", "q_impsess_abc_0002"],
  // This question is not surfaced to the user until all prerequisites reach status ANSWERED or SKIPPED.
  // BATCH questions have no prerequisites.
  // ENTITY questions depend on BATCH questions.
  // ROW_GROUP questions depend on their parent ENTITY question.
  // ROW questions depend on their parent ROW_GROUP question (if applicable).

  // ── Question content (deterministic template; not LLM-generated) ──────
  "question_text": "Which security in your catalog is 'Coca Cola Company'?",
  "context_text": "14 rows spanning 2018–2021 use this company name.",
  "hint_text": null,                 // optional clarification (e.g., "Check for cross-listed variants")
  "affected_row_count": 14,
  "affected_years": [2018, 2019, 2020, 2021],

  // ── Candidates (SECURITY_IDENTITY questions only) ─────────────────────
  "candidates": [
    {
      "candidate_id": "cand_0001",
      "security_id": "KO:XNYS",
      "display_name": "The Coca-Cola Company",
      "ticker": "KO",
      "exchange_mic": "XNYS",
      "exchange_name": "NYSE",
      "trading_currency": "USD",
      "isin": "US1912161007",
      "status": "ACTIVE",

      // Matching pipeline provenance:
      "match_stage": "STAGE_4_NORMALIZED_NAME",  // STAGE_1 | STAGE_2 | STAGE_3 | STAGE_4 | STAGE_5
      "stage_score": 0.87,           // for STAGE_4; null for other stages
      "ai_confidence": "HIGH",       // null if this candidate did not come from AI
      "ai_reasons": [
        "'coca cola' is a strong normalized match for 'The Coca-Cola Company'",
        "Company is known to pay dividends; consistent with DIVIDEND import context"
      ],
      "ai_call_id": "llmcall_xyz789" // provenance link; null if AI did not contribute
    },
    {
      "candidate_id": "cand_0002",
      "security_id": "COKE:XNAS",
      "display_name": "Coca-Cola Consolidated Inc.",
      "ticker": "COKE",
      "exchange_mic": "XNAS",
      "exchange_name": "NASDAQ",
      "trading_currency": "USD",
      "isin": "US1912161025",
      "status": "ACTIVE",
      "match_stage": "STAGE_5_AI_ADVISORY",
      "stage_score": null,
      "ai_confidence": "LOW",
      "ai_reasons": ["Name overlap; however Consolidated is a distinct entity from The Coca-Cola Company"],
      "ai_call_id": "llmcall_xyz789"
    }
  ],
  "allow_create_new_security": true,
  "allow_skip": true,

  // ── Options (non-SECURITY_IDENTITY questions) ─────────────────────────
  // For multiple-choice questions (ROW_GROUP and BATCH scopes).
  "options": null,
  // Example for RIGHTS_AMOUNT_CLASSIFICATION:
  // "options": [
  //   { "option_id": "OPT_ENTITLEMENT",  "label": "Declared entitlement / gross value of rights" },
  //   { "option_id": "OPT_RIGHTS_SOLD",  "label": "Proceeds from selling residual rights" },
  //   { "option_id": "OPT_SHARE_VALUE",  "label": "Value of shares received" },
  //   { "option_id": "OPT_UNKNOWN",      "label": "Unknown — import as pending classification" }
  // ]

  // ── Status ────────────────────────────────────────────────────────────
  "status": "PENDING",
  // Enum: PENDING | ANSWERED | SKIPPED | SUPERSEDED | CREATION_IN_PROGRESS
  // CREATION_IN_PROGRESS: user has submitted the "create new security" form; the
  //   four-step write sequence (security-identity-import-matching.md §10.3) is in progress.
  //   The question cannot be re-answered until creation completes or is cancelled.
  // SUPERSEDED: this question was invalidated by a prior answer change

  // ── Inline security creation — crash-recovery intent ─────────────────
  // Populated only while status = CREATION_IN_PROGRESS.
  // Cleared (set to null) when creation completes (status → ANSWERED) or is cancelled (status → PENDING).
  "pending_creation": null,
  // Structure when populated:
  // {
  //   "idempotency_key": "creat_{session_id}_{empresa_normalized}",
  //   "intended_security_id": "IBE:XMAD",
  //   "intended_ticker": "IBE",
  //   "intended_mic": "XMAD",
  //   "intended_display_name": "Iberdrola S.A.",
  //   "intended_isin": "ES0144580Y14",     // null if not provided
  //   "intended_trading_currency": "EUR",
  //   "empresa_normalized_source": "iberdrola",
  //   "started_at": "2026-09-05T11:08:00Z",
  //   "last_step_completed": 2,           // 0 = not started; 1 = security_master written;
  //                                       // 2 = alias written; 3 = session updated; 4 = question answered
  //   "stale_after_minutes": 5            // treat as stalled if IN_PROGRESS longer than this
  // }
  // See security-identity-import-matching.md §10.3 for the full write protocol.
  // The session service reads this on load and resumes from last_step_completed + 1 if stale check passes.

  // ── Answer ────────────────────────────────────────────────────────────
  "answer": null,
  // Populated when status = ANSWERED. Structure:
  // {
  //   "answered_at": "...",
  //   "answered_by": "dsanchor",
  //   "answer_type": "SELECTED_CANDIDATE",
  //   // Enum: SELECTED_CANDIDATE | CREATED_NEW_SECURITY | SKIPPED_COMPANY |
  //   //       SELECTED_OPTION | ACCEPTED_DEFAULT | EXTENDED_MAPPING
  //   "selected_candidate_id": "cand_0001",    // for SELECTED_CANDIDATE
  //   "resolved_security_id": "KO:XNYS",      // computed from answer
  //   "selected_option_id": null,              // for SELECTED_OPTION
  //   "new_security_created": false,
  //   "new_security_id": null,
  //   "alias_written": true,                   // whether a durable alias was added to security_master
  //   "alias_idempotency_key": "alias_{empresa_normalized}_{security_id}"
  // }

  "answer_history": [],
  // Append-only list of prior answer objects.
  // When the user changes an answer, the current answer is appended here
  // and the new answer replaces `answer`. Never delete history.

  // ── Revalidation metadata ─────────────────────────────────────────────
  "invalidates_validation_run_ids": [],
  // Populated when this answer is changed: lists all valrun_ids that became stale.
  // Used to ensure the UI never shows a preview computed before the last answer change.

  "supersedes_question_id": null,
  // If this question was generated to replace a SUPERSEDED question
  // (e.g., a new ENTITY question after a prior one was voided), this field links back.

  "generated_at": "2026-09-05T11:00:05Z",
  "_etag": "\"0x8D...\""
}
```

---

## 7. Preview Snapshot

The preview snapshot is computed when the session reaches `PREVIEW_READY` (all questions answered, zero blocking validation issues). It is stored inside the session document (or as a companion document if it exceeds ~200 KB).

The preview is **immutable** once computed: the service computes it deterministically from the answered questions and staged rows, then writes it once. If an answer changes, the preview is nullified and must be regenerated.

The preview contains **no individual financial amounts by row** — only aggregates by security and year. Individual movement previews (light-weight representations of each ledger record that would be created) are included in a separate `preview_movements` companion document if row count > 50.

```jsonc
"preview_snapshot": {
  "generated_at": "2026-09-05T11:09:00Z",
  "validation_run_id": "valrun_impsess_abc_005",  // the validation pass that certified this preview
  "rows_to_commit": 74,
  "rows_to_exclude": 1,                            // unanswered ROW questions
  "rows_skipped": 9,
  "rows_all_zero": 2,

  // Per-security summary (no per-row amounts):
  "security_summary": [
    {
      "security_id": "KO:XNYS",
      "display_name": "The Coca-Cola Company",
      "row_count": 14,
      "import_type": "DIVIDEND",
      "total_gross_eur": "987.34",          // sum of all rows; 2dp for display
      "total_net_eur": "756.82",
      "total_wht_origin_eur": "148.10",
      "total_wht_dest_eur": "82.42",
      "years": [2018, 2019, 2020, 2021]
    }
  ],

  // Year summary:
  "year_summary": [
    { "year": 2018, "row_count": 18, "total_gross_eur": "234.56", "total_net_eur": "178.90" }
  ],

  // Grand totals:
  "grand_total_gross_eur": "3421.78",
  "grand_total_net_eur": "2619.34",

  // Movements companion reference (if stored separately):
  "movements_preview_doc_id": null,    // "prevmov_{session_id}" if separate doc

  // Disclosure:
  "preview_note": "These are the ledger records that will be created. No records have been written yet. Confirm below to commit."
}
```

---

## 8. `llm_call_record` Document Schema

Every LLM call is recorded for full provenance. The model never receives financial amounts.

```jsonc
{
  "id": "llmcall_{ulid}",
  "session_id": "impsess_abc",       // partition key
  "doc_type": "llm_call_record",

  "called_at": "2026-09-05T11:00:08Z",
  "model_identifier": "...",          // model name/version used
  "task": "SECURITY_MATCH_SUGGESTION",
  // Enum: SECURITY_MATCH_SUGGESTION
  // (the only LLM task in scope; future tasks may include QUESTION_WORDING_HINT)

  // ── Sanitized input (stored for audit; no financial data) ─────────────
  "input": {
    "empresa_raw": "Coca Cola Company",
    "empresa_normalized": "coca cola",
    "context": {
      "import_type": "DIVIDEND",
      "years_present": [2018, 2019, 2020, 2021],
      "country_hint": "ES",           // from batch metadata if available; null otherwise
      "row_count": 14                 // structural fact; no amounts
    },
    "existing_candidates": [
      // Only structural fields from security_master; no financial data:
      {
        "security_id": "KO:XNYS",
        "display_name": "The Coca-Cola Company",
        "ticker": "KO",
        "exchange_mic": "XNYS",
        "trading_currency": "USD",
        "isin": "US1912161007",
        "status": "ACTIVE"
      }
    ]
  },
  // What the LLM MUST NOT receive (enforced by the API layer, not prompting):
  // - Any monetary amount (gross, net, withholding, fees, quantity)
  // - Any individual row data
  // - Any FX rates
  // - Any dates of individual transactions (only years_present as an aggregate hint)

  // ── Raw LLM output ────────────────────────────────────────────────────
  "output_raw": {
    "proposals": [
      { "security_id": "KO:XNYS",   "confidence": "HIGH", "reasons": ["..."] },
      { "security_id": "COKE:XNAS", "confidence": "LOW",  "reasons": ["..."] }
    ],
    "notes": "The Coca-Cola Company is almost certainly KO:XNYS."
  },

  // ── Validated output (after catalog verification) ─────────────────────
  "output_validated": {
    "proposals_retained": 2,
    "proposals_dropped_count": 0,    // any invented security_id not in catalog is dropped here
    "dropped_security_ids": []
  },

  "ttl": 604800,                     // expires with the session (7 days)
  "linked_question_id": "q_impsess_abc_0004"
}
```

**Sanitization enforcement:** The LLM call is made by a dedicated `SecurityMatchSuggestionService` that constructs the input from the entity group metadata only. The service holds the parsed rows but never passes amounts to the LLM input builder. This is enforced structurally (the builder has no access to the row amount fields at the type level) — not purely by prompting.

---

## 9. Changing an Answer — Revalidation Chain

When the user changes a previously-answered question, the state machine executes a deterministic revalidation chain:

### Step 1 — Persist the Answer Change

Using a conditional write (`if-match: {current_etag}`):
- Append the old answer to `answer.answer_history[]`.
- Write the new answer to `answer.answer`.
- Set `status: ANSWERED` (or the new status if the answer is now a skip).
- Populate `invalidates_validation_run_ids` with all validation run IDs computed after the prior answer.

If the conditional write fails (concurrent modification), retry from re-read.

### Step 2 — Identify Downstream Dependencies

The service loads the session's full question inventory and finds all questions whose `prerequisite_question_ids` includes the changed question's ID. For each dependent question:
- If the dependency is an ENTITY question that has been re-answered with a different `security_id`: mark all ROW_GROUP questions for the old `security_id` as `SUPERSEDED`. Generate new ROW_GROUP questions for the new `security_id`.
- If the dependency is a BATCH question (e.g., `import_type` changed): all ENTITY and GROUP questions are `SUPERSEDED`; regenerate from scratch.
- If the dependency is a ROW_GROUP question that was `SUPERSEDED`: its ROW children (if any were generated) are also `SUPERSEDED`.

### Step 3 — Invalidate the Preview

If `preview_snapshot` is not null: set it to null. Set `validation_clean: false`.

### Step 4 — Determine Regression Target State

The service scans all questions:
- If any BATCH question is unanswered → regress to `BATCH_QUESTIONS`
- Else if any ENTITY question is unanswered or `SUPERSEDED` → regress to `ENTITY_QUESTIONS`
- Else if any ROW_GROUP question is unanswered or `SUPERSEDED` → regress to `ROW_GROUP_QUESTIONS`
- Else if any ROW question is unanswered → regress to `ROW_QUESTIONS`
- Else → advance to `VALIDATING`

The state machine write uses `if-match` to ensure no concurrent writes occurred between steps.

### Step 5 — Re-run Validation

Validation is a synchronous deterministic pass (no LLM). It re-evaluates:
- All arithmetic checks for rows whose `security_id` or classification changed
- Dedup checks (cross-ledger and within-batch)
- Date/year mismatch checks
- Non-financial structural checks

If validation is clean: advance to `PREVIEW_READY` and compute a new `preview_snapshot`.

### Invariant

The same sequence of questions and answers always produces the same `preview_snapshot`. Revalidation never produces different results from the same inputs. The LLM contributes only to candidate suggestions (which are structural, not financial) and its output is immutably stored in `llm_call_record`. A revalidation triggered by an answer change does not re-call the LLM unless a new entity group appears (new distinct `empresa_normalized` not previously seen).

---

## 10. Commit Protocol

The commit is a two-phase operation gated on explicit user confirmation.

### Phase A — Confirmation Gate

The user clicks "Confirm and import" in the UI. The service:
1. Verifies `state = PREVIEW_READY` and `validation_blockers_count = 0` (conditional read).
2. Writes `commit_confirmed_at`, `commit_confirmed_by`, and transitions state to `COMMIT_CONFIRMED` using a conditional write.
3. Returns 202 Accepted. The actual ledger writes happen in Phase B.

No ledger records are written in Phase A.

### Phase B — Ledger Writes

The service iterates the committed rows (in session order, for reproducible IDs) and writes each `ledger_txn` or `ca_event`+`ca_leg` set to `portfolio_ledger`:
- Each write uses the row's `row_sha256` as the idempotency key (checked before write).
- Each successful write appends the new document ID to `committed_movement_ids[]` in the session document.
- The `committed_movement_ids[]` write is also a conditional update (using `_etag`) to handle concurrent retries.

If Phase B is interrupted (crash, network failure), the session remains in `COMMIT_CONFIRMED`. On resume:
- The service reads `committed_movement_ids[]` and cross-references against the ledger (by `row_sha256`).
- Already-written records are skipped.
- Remaining records are written.
- Phase B is idempotent: running it N times produces the same result as running it once.

### Phase C — Finalization

When all rows are processed:
1. Write the `import_batch` document to `portfolio_ledger` (the batch-level record linking all committed movement IDs).
2. Set session state to `COMMITTED`.
3. Update `portfolio_tracked: true` on all `security_master` documents for newly acquired securities (best-effort; may run asynchronously).
4. Clear the session's `ttl` field (no auto-expiry for committed sessions — they are the permanent audit record of the import conversation).

Committed sessions are immutable: no further state transitions, no answer changes.

---

## 11. Concurrency and Idempotency

### 11.1 Optimistic Concurrency

All writes to `import_session` and `import_question` documents use Cosmos conditional writes (`if-match: {_etag}`). If a concurrent modification is detected (HTTP 412 Precondition Failed), the service:
1. Re-reads the current document.
2. Re-evaluates whether the intended operation is still applicable (it may have already been applied by the other writer).
3. If already applied: return the current state (no-op).
4. If not yet applied: retry the conditional write with the new `_etag`.

Maximum retry attempts: 5, with exponential back-off (50ms, 100ms, 200ms, 400ms, 800ms). After 5 failures: return HTTP 409 Conflict to the caller.

### 11.2 Duplicate Answer Submission

If the user submits the same answer twice (e.g., double-click or browser retry):
- The service reads the current question status.
- If `status = ANSWERED` and the new answer is identical (same `answer_type` + same choice): return the current state as a no-op. No document write, no 409.
- If the new answer differs: proceed with the answer-change flow (§9).

Idempotency key for ENTITY answer: `{session_id}|{empresa_normalized}|{resolved_security_id}`.

### 11.3 Duplicate Session for Same File

If the user uploads the same file twice (same `content_hash_sha256`):
- The service checks for an existing session with the same `file.content_hash_sha256` and `created_by`.
- If found and `state != COMMITTED` and `state != EXPIRED`: return the existing session (resume it).
- If found and `state = COMMITTED`: return a "This file has already been imported" response with a link to the prior committed session.
- If found and `state = EXPIRED`: allow a new session (the prior one lapsed without commit).
- If not found: create a new session.

This prevents accidental duplicate imports from the same source file.

### 11.4 Concurrent Question Answering from Two Browser Tabs

If the same user has the import session open in two tabs and answers different questions concurrently:
- Tab A answers question 4; Tab B answers question 5 simultaneously.
- Both are conditional writes on their respective `import_question` documents (separate documents → no conflict between them).
- Both then attempt to update `questions_answered` on the session document → conditional conflict on one write → retry → merge correct count.
- The session state transition (e.g., "all questions answered → advance to VALIDATING") is evaluated after each question write; only the final question to be answered triggers the transition.

The state transition is safe because it is always computed from the full question inventory (a point-in-time read), not from an incremented counter.

---

## 12. Session Resume and Expiry

### 12.1 Resuming an Incomplete Session

The import history page lists all sessions in non-terminal states (`FILE_PARSED`, `BATCH_QUESTIONS`, `ENTITY_QUESTIONS`, `ROW_GROUP_QUESTIONS`, `ROW_QUESTIONS`, `VALIDATING`, `PREVIEW_READY`) with their expiry dates.

When the user clicks "Resume":
1. The service fetches the session document and all associated question documents (single-partition read).
2. The service determines the current question to show: the lowest-sequence-number question with `status = PENDING` whose prerequisites are all satisfied.
3. The TTL is extended: `ttl` is updated to 7 days from the resume time; `expires_at` is updated accordingly; `resumed_count` is incremented.
4. The UI renders the current question state.

No re-parsing of the original file is needed. The parsed row data lives in the `staged_import_row` documents in `portfolio_ledger` (partition `_unassigned` or the assigned account), not in the session. The session references rows by number; the staging documents hold the parsed content.

### 12.2 Expiry Behavior

When a session's TTL elapses (CosmosDB deletes the document):
- All associated `import_question` and `llm_call_record` documents are also deleted (same partition, same TTL value — set identically when the session is created).
- The `staged_import_row` documents in `portfolio_ledger` (separate container) have their own TTL (90 days) and are not affected by session expiry. They will expire on their own schedule.
- No ledger records exist (none were written before commit).
- The file, if `store_raw_file = false`, was already discarded after parsing.

If the user returns after session expiry: they see "This import session expired. Please re-upload the file to start a new session." The only recovery path is re-upload.

### 12.3 Session Termination Without Commit

The user may explicitly abandon a session without committing (click "Cancel import"). This is a soft deletion:
- State transitions to a terminal `CANCELLED` state.
- `ttl` is set to `86400` (24-hour cleanup delay, so the user could undo immediately if clicked by mistake).
- On cancel confirmation, the session moves to `CANCELLED` and the short TTL ensures cleanup.

---

## 13. Inline vs. Separate Question Documents

The session document has a 2 MB document size limit in Cosmos. For files with many distinct companies, the combined question document (with candidates, answer history, etc.) can exceed this.

**Threshold rule:**
- If `entity_groups_count + row_groups_count ≤ 15`: embed all questions inline in the session document (`questions_inline: true`).
- If count > 15: store each question as a separate `import_question` document in the same partition (`questions_inline: false`). The session document stores only question IDs and a lightweight status summary.

**Reading the full session state:**
- When `questions_inline = true`: single document read.
- When `questions_inline = false`: one session document read + one cross-partition query `SELECT * FROM c WHERE c.session_id = @sid AND c.doc_type = 'import_question'` — all in the same physical partition so this is a single-partition read, not a cross-partition query.

The transition from inline to separate is one-way and triggered at session creation time (when the entity group count is known). It is not re-evaluated mid-session.

---

## 14. Relationship to Prior Import Contracts

This state machine operates as the **coordination layer** over the three existing import contracts. The prior documents (dividend, purchase, sales CSV import) defined what a fully-resolved, validated import row looks like in the ledger. This document defines how the system gets from an uploaded file to that fully-resolved state.

Key integration points:

| Prior contract decision | How the state machine uses it |
|------------------------|-------------------------------|
| `row_sha256` dedup key | Row hash is computed during `FILE_PARSED` from the raw bytes; stored on each `staged_import_row`; used during Phase B commit for idempotency |
| `empresa_normalized` normalization pipeline | ENTITY group membership is determined by the same normalization used in the prior contracts; no second normalization standard |
| `WARNING_*` codes on ledger records | Validation codes surfaced in `validation_warnings[]` during the session become the `import_status[]` values written to ledger records at commit time; what was a question in the session becomes a resolved or noted status on the permanent record |
| `_unassigned` partition | All staged rows (in `portfolio_ledger`) use the session's `account_id` (default `_unassigned`) as their partition key; the commit writes ledger records to the same partition |
| `PENDING_SCRIP_CLASSIFICATION` reconciliation status | This is the result of a user explicitly choosing "Import as pending — classify later" on a `ZERO_COST_CLASSIFICATION` question; the session records the choice, the commit writes the ledger record with this reconciliation status |
| Security required, account optional | Enforced by the state machine: ENTITY questions are blocking (the session cannot reach `PREVIEW_READY` while any entity group has `resolution_status = PENDING`); account is never required, never questioned by default |

---

## 15. Session-Side Inline Security Creation

This section describes how the import session state machine coordinates with the four-step creation protocol defined in `livingston-security-identity-import-matching.md §10`. That document owns the write protocol and collision checks; this section owns the session-state transitions.

### 15.1 State During Creation

When the user submits the "create new security" form within an ENTITY question:

1. The service runs collision checks (Check A: ticker+MIC point read; Check B: ISIN cross-partition query if ISIN provided). These are reads; no state change yet.
2. If collision-free: write `pending_creation` to the question document and set `question.status = CREATION_IN_PROGRESS` (conditional write, `_etag`). The session state does not change — it remains in `ENTITY_QUESTIONS`.
3. Execute Steps 1–4 of the write sequence (see §10.3 of the identity doc), updating `pending_creation.last_step_completed` after each step.
4. On successful Step 4: write `question.status = ANSWERED`, `question.answer = { answer_type: CREATED_NEW_SECURITY, ... }`, clear `question.pending_creation = null` — all in a single conditional patch on the question document.
5. The session service then re-evaluates question completion and advances the session state normally (same path as `SELECTED_CANDIDATE` answer).

If a collision is detected at step 1: the form is not submitted; the service instead adds the colliding `security_master` as a new candidate entry in `question.candidates[]` (conditional patch on the question document) and returns the updated candidate list to the UI. The UI presents the collision result to the user. `question.status` remains `PENDING`.

### 15.2 Questions That Block During Creation

While a question has `status = CREATION_IN_PROGRESS`:
- The question cannot be re-answered or skipped by the user.
- No other ENTITY questions or ROW_GROUP questions that depend on this question can advance.
- The session state machine treats `CREATION_IN_PROGRESS` as equivalent to `PENDING` for the purpose of computing questions remaining — the session does not advance until the question reaches `ANSWERED` or `PENDING` (after a cancelled creation).

### 15.3 Stale Creation Recovery

On session load (initial load or resume), the service checks every question with `status = CREATION_IN_PROGRESS`:
1. Read `pending_creation.started_at` and `pending_creation.stale_after_minutes`.
2. If `now - started_at < stale_after_minutes`: the creation is still live (another in-flight request is processing it). Do not interfere.
3. If `now - started_at ≥ stale_after_minutes`: the creation has stalled. The service resumes from `pending_creation.last_step_completed + 1` using the stored `creation_intent` fields. Each step's idempotency check prevents double-writes.
4. If resumption reaches Step 4 successfully: the question is marked `ANSWERED`.
5. If resumption fails (e.g., the `symbols` container write fails again): `pending_creation.last_step_completed` is updated to the last successful step; the service will retry on the next session access. After 3 consecutive failed resumption attempts (tracked in `pending_creation.resumption_attempts`), the service surfaces a "Creation failed — retry or cancel" option to the user.

### 15.4 Cancelling an In-Progress Creation

If the user cancels a creation (or the service surfaces the failed-creation option):
1. The service checks `pending_creation.last_step_completed`:
   - If 0 (nothing written): simply set `question.status = PENDING`, `question.pending_creation = null`. The user is returned to the ENTITY question with the original candidate list.
   - If ≥ 1 (security_master was written): the `security_master` document in `symbols` container is **not rolled back**. A partially-created security_master with no alias and no ledger references is harmless. The user can map to it later or ignore it. The session simply moves the question back to `PENDING` with a note: "A security entry for {ticker}:{MIC} may have been partially created. Check the security catalog if needed."
2. Set `question.pending_creation = null`, `question.status = PENDING`.
3. Add the partially-created security (if Step 1 completed) as a candidate in `question.candidates[]` for user convenience.

### 15.5 Cross-Tab Race During Creation

If two browser tabs both try to start a creation for the same ENTITY question simultaneously:
- Tab A: writes `question.status = CREATION_IN_PROGRESS` first (conditional write with `_etag` succeeds).
- Tab B: attempts the same conditional write → 412 Precondition Failed (the `_etag` has changed). The service re-reads the question, finds `status = CREATION_IN_PROGRESS`, and returns that state to the Tab B UI: "A security is being created for this company — please wait."
- Tab B does not start a second creation. Once Tab A's creation completes (question status → `ANSWERED`), Tab B's next polling/push update shows the resolved state.

This is consistent with the concurrent-tab collision handling described in `livingston-security-identity-import-matching.md §10.5`.

### 15.6 Catalog Purity After Creation

After a successful creation, the new `security_master` is in the `symbols` container. The entity group's `resolved_security_id` in the session document is a reference to it. No security identity data lives in the `import_sessions` container: `pending_creation` is cleared, and the only security-identity fields remaining in the session are `resolved_security_id` (string reference) and the embedded candidate metadata in `import_question.candidates[]` (read-only provenance; not used for any authoritative lookup).

Any future import session (different file, different user session) that encounters the same company name will find the new security through the Stage 1 alias match (the alias was written to `security_master.aliases[]` at Step 2) — without any reference to the original creating session.

---

## 16. Open Questions

| # | Question | Impact |
|---|----------|--------|
| Q1 | The `staged_import_row` documents in `portfolio_ledger` and the `import_session` documents in `import_sessions` both hold representations of the same parsed row. Is this duplication acceptable, or should the session document simply reference the staging document IDs? The current design keeps the session self-contained (for resume without cross-container reads) but doubles the storage for large files. | Storage vs. simplicity |
| Q2 | When the file is XLSX, parsing happens server-side. Large XLSX files (>5 MB, thousands of rows) may exceed a reasonable API request size. Should the API support chunked upload (multipart) or a server-side URL reference (user uploads to blob storage, session references the blob)? The current design assumes direct upload. | Upload infrastructure |
| Q3 | The `ACCOUNT_ASSIGNMENT` BATCH question is described as "not asked by default." If asked, the account assignment affects which partition the staging rows and committed records go to. This means the account decision must be made in the BATCH_QUESTIONS phase before any rows are staged. Should the account question always be the first question in a session if it is asked at all? | UX sequencing |
| Q4 | The 7-day session TTL is a design choice. For users with large files and many unresolved companies, 7 days may not be enough if they are waiting on external information (e.g., confirming which exchange a foreign holding was on). Consider a user-configurable TTL extension or a "pause indefinitely" option. | UX and operations |

---

## 17. Summary

The `import_session` document is the single stateful record for one conversational import. It lives in a dedicated `import_sessions` container (partition key `/session_id`), isolated from the permanent ledger. The seven non-terminal states (`FILE_PARSED` through `PREVIEW_READY`) represent progressive question resolution; the terminal states (`COMMITTED` and `EXPIRED`) are immutable.

**Question scopes** enforce proportionate fan-out: BATCH answers configure the whole file; ENTITY answers resolve a company name to a `security_id` for every row sharing the exact normalized key; ROW_GROUP answers resolve a structural ambiguity for every row sharing the exact fingerprint; ROW answers handle individual exceptions. No answer propagates to merely similar groups — only to identical ones.

**The LLM** is called only for `SECURITY_MATCH_SUGGESTION` (Stage 5 of the matching pipeline) and receives only structural company-name and catalog metadata — never financial amounts, row data, or individual dates. Its output is validated against the existing catalog, recorded in a `llm_call_record` document for full provenance, and presented to the user as advisory candidates. The user's confirmation creates the durable alias on `security_master`.

**Inline security creation** from within the import conversation follows the four-step idempotent write sequence in `livingston-security-identity-import-matching.md §10`. The question takes `CREATION_IN_PROGRESS` status while writes are in progress. Crash recovery uses `pending_creation.last_step_completed` to resume from the last successful step. Concurrent-tab races are resolved safely by `_etag` conditional writes. After creation, all identity data lives in the `symbols` container — no security identity artifacts remain in `import_sessions`.

**No ledger record is written before the user explicitly confirms commit.** The commit is idempotent: each record write uses `row_sha256` as the dedup key, and partial failures are recovered by resuming from `committed_movement_ids[]`. Optimistic concurrency (`_etag` conditional writes) guards all state transitions against concurrent modification. Duplicate file uploads and duplicate answer submissions are handled as no-ops, not errors.

**Security is a hard gate**; account defaults silently to `_unassigned` and is never asked unless the user has configured accounts and opted in to the account-assignment prompt.
