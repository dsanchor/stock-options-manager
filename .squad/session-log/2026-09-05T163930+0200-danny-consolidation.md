# Danny — Session Log: Portfolio & Dividend Architecture Consolidation

**Date:** 2026-09-05  
**Role:** Lead Architect  
**Task:** Consolidate specialist designs; resolve conflicts; establish authoritative ca_event/ca_leg model  
**Status:** COMPLETE  
**Output:** Two documents — Scrip Rights Architecture & Dividend CSV Import Consolidation

---

## Context

Three user directives arrived on 2026-09-05 concerning scrip dividends, rights handling, and historical import. Four specialist teams submitted conflicting designs:

- **Livingston:** Detailed persistence model (partition strategy, account reassignment, three-layer dedup)
- **Rusty:** UX wizard for CSV import (delimiter detection, four-step flow, alerts)
- **Basher:** Validation test matrix (error taxonomy, edge cases, adversarial scenarios)
- **Prior architecture (Decision #1):** Simple mixed dividend model (two legs: cash + shares)

**Problem:** Prior model assumed complete scrip events (cash + shares); user directives revealed real events include:
1. Residual rights sales (with own withholding)
2. Cash top-ups to round up to whole shares
3. Separate cost-basis determination (NOT auto-derived from top-up or reference price)

Specialist designs had conflicting assumptions on:
- Rights warning aging (should old warnings be hidden?)
- Broker/account optionality (safe import without immediate account assignment?)
- Cost basis handling (auto-derive from FMV or top-up?)
- Dedup key design (should account be in the key?)

---

## Approach

1. **Read all three directives** → Identified user's hard constraints
2. **Read all specialist documents** → Catalogued each team's assumptions and conflicts
3. **Identify gaps in prior model** → FMV/cost-basis conflation, no rights-sold leg, no top-up leg
4. **Synthesize ca_event + ca_leg model** → Parent document + leg subtypes, each with clear responsibilities
5. **Resolve nine major conflicts** (RC-1 through RC-9) → Documented reasoning for each
6. **Specify six final invariants** → Checked each against user directives and specialist input
7. **Create authoritative consolidated documents** → Two comprehensive specs superseding specialist drafts

---

## Key Decisions

### 1. FMV / Cost Basis Separation (Correcting Prior Revision)

**Problem:** First draft of this document incorrectly asserted `cost_basis_per_share = company_ref_price` as a system invariant. This conflates broker-statement fact (FMV) with tax interpretation (cost basis).

**User directive:** "Top-up cash must not be conflated with cost basis."

**Solution:** Store three independent fields on `SHARE_ACQUISITION` leg:
- `fmv_per_share` (broker-statement fact; immutable)
- `cost_basis.cost_per_share_eur` (tax interpretation; user-set; may change)
- Cross-reference to `CASH_TOP_UP` leg (navigational only; does NOT determine cost basis)

**Why:** Different jurisdictions treat cost basis differently. System stores facts; never chooses tax treatment.

### 2. Rights Warning Persistence (Overruling Livingston's "Info" Level)

**Livingston's position:** `PENDING_RIGHTS_CLASSIFICATION` is "Info" level; not in warning badges; not in summary counts; old rows have no badge.

**User directive:** "Every row with Importe en Derechos > 0 must remain marked with a warning until manually fixed. This applies to older years too."

**Resolution:** WARNING level badge, persistent on all years.

**Clarification of "non-blocking older years":**
- Warning does NOT prevent import of new data
- Warning does NOT suppress row from dashboards or aggregates
- Warning does NOT appear as global banner
- Warning DOES appear on row itself in any view showing it
- Reconciliation queue defaults to 2026 (user's active year) but older rows accessible via year filter with badges intact

**Impact:** Older incomplete scrip entries may take months to resolve (waiting for broker statements). Persistent badge keeps them visible without suppressing portfolio activity.

### 3. Broker/Account Optionality (Unanimous; Carried Forward)

**All three specialists agreed:** Missing broker/account produces no warning.

**Implementation:** `account_id = "_unassigned"` partition value. Administrative state, never a data-quality flag.

**Account reassignment:** Livingston's cross-partition move workflow (write to target, void original, idempotent retry, orphan detection via row_hash).

### 4. Three-Layer Deduplication (User Directive Codified)

**User:** "Idempotent retry via batch+row+normalized hash. Cross-batch matches = WARNING, not auto-suppression."

**Solution:**
- Layer 1: `batch_id + row_number + row_hash` → SKIP silently (retry-safe)
- Layer 2: `row_hash` within file → ERROR if repeated (user must fix)
- Layer 3: `row_hash` cross-batch → WARNING; user decides (never auto-collapse)

**Why account NOT in key:** May be null; legitimate multi-broker identical payments must not collapse.

### 5. Column Mapping — Definitive Specification

All specialists agreed on 8-column format. Coded explicitly:
- Positional-first (columns 0–7 of each data row)
- Header-name fallback (detect and skip header if present)
- Columns 9+ silently ignored (enables copy-paste from wider spreadsheets)
- Spanish locale parsing (period = thousands, comma = decimal)
- Date validation (DD/MM/YYYY, cross-check Año field)

### 6. Phase Placement — Phase 1b

**Rationale:** Unassigned-account design means broker setup is not prerequisite. User's engagement depends on backfill. Phase 1b (immediately after Phase 1 manual MVP) places import at critical engagement moment.

---

## Conflict Resolutions (Nine Major Conflicts)

| RC | Conflict | Livingston | Rusty | Basher | Decision | Rationale |
|----|----------|-----------|-------|--------|----------|-----------|
| RC-1 | Container name | `portfolio_ledger` | defer | defer | `portfolio` (from Decision #1) | Phase 1 baseline; not reopened |
| RC-2 | Rights warning age | Info level; old rows no badge | warn + badge all | 🟡 warning | WARNING + persistent on all years | User directive explicit |
| RC-3 | Broker/account missing | No warning | No warning | No warning | `_unassigned` partition | Unanimous |
| RC-4 | Dedup key | row_sha256 + semantic | security_id based | date/company/gross | Three-layer model | User directive + multi-broker concern |
| RC-5 | Source currency | Required batch-level | Required | (defers) | Required dropdown, EUR default | Only broker optional (user implicit) |
| RC-6 | Warning hierarchy | Scattered definitions | Integrated into wizard | Matrix outline | Definitive taxonomy (6 blocking, 8 warning) | Synthesized from all three |
| RC-7 | Column mapping | 8-column, positional | 8-column, positional | 8-column, positional | Columns 0–7 positional, 9+ ignored | Unanimous |
| RC-8 | Dest WHT null vs zero | `batch_captures_dest_wht` param | (defers) | (defers) | Same param, conservative default | Admin workflow handles later |
| RC-9 | Phase placement | Phase 1b | Phase 1.5 | (defers) | Phase 1b | User directive + backfill rationale |

---

## Document Outputs

### 1. danny-scrip-rights-topup-architecture.md (480 lines)

**Scope:** Scrip dividend lifecycle, FMV/cost-basis separation, ca_event + ca_leg model, four-leg types.

**Key sections:**
- §1: Problem statement (what prior model missed: rights sold, top-up, cost-basis separation)
- §2: Design principles (one event + multiple legs; each leg has own withholding; broker facts ≠ tax interpretation)
- §3: Document model (ca_event schema, ca_leg subtypes, SHARE_ACQUISITION with separate FMV + cost_basis, CASH_TOP_UP pure outflow)
- §4: FMV vs. cost basis field design (table: what each field is, who supplies it, can it change)

**Critical correction:** This version corrects prior draft's erroneous tax-invariant assertion. Cost basis is user-determined, not system-derived.

### 2. danny-dividend-csv-import-consolidated.md (510 lines)

**Scope:** Conflict resolutions, import contract, validation, deduplication.

**Key sections:**
- §1: Nine conflict resolutions (RC-1 through RC-9)
- §2: Batch metadata (source_currency, fx_behavior, account_id, etc.)
- §3: Row classification (CASH_DIVIDEND, DIVIDEND_WITH_SCRIP, SCRIP_DIVIDEND, RIGHTS_OR_SHARE_PENDING)
- §4: Import batch provenance (audit trail, import_batch_id tracking)
- (Continues in merged decisions.md)

---

## Handed Off To

**Livingston:** Detailed Cosmos schema, partition strategy, account-reassignment transaction workflow, test fixtures.

**Rusty:** Frontend wizard implementation; Step 0–3 flow; delimiter detection; alert/badge rendering; reconciliation form UX.

**Linus:** Backend import controller; validation logic; row-to-ca_event mapping; cost-basis state management.

**Basher:** Test automation; error-case fixtures; adversarial scenarios (mixed encodings, malformed CSVs, large datasets).

---

## Status

✅ **COMPLETE** — All conflicts resolved; consolidated documents ready for implementation phase.

