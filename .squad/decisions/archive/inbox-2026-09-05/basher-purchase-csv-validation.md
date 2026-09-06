# Purchase CSV Import — Synthetic Test Matrix & Acceptance Criteria

**Author:** Basher (Tester/Reviewer)  
**Date:** 2026-09-05  
**Scope:** 7-column purchase importer: `Año | Empresa | Fecha compra | Valor compra | Acciones | Total (€) | Comisión`  
**Constraint:** All fixtures are fully synthetic. No real user financial rows reproduced.

> **Amendment 2026-09-05T17:12:18+02:00:** UNRESOLVED_SECURITY is now **BLOCKING** for all import types. No BUY, SELL, or DIVIDEND row may commit without a confirmed canonical `security_id`. Exact durable aliases auto-resolve (Tier 2); AI/fuzzy suggestions are advisory and require explicit per-row confirmation before the block lifts. Account field remains optional — no warning generated.

---

## Semantic contract (derived from directive)

| Symbol | Meaning |
|--------|---------|
| `VP` | Valor compra — unit price per share |
| `Q` | Acciones — quantity (may be fractional) |
| `T` | Total (€) — cost basis excl. commission (≈ VP × Q) |
| `C` | Comisión — broker fee |
| `CF` | Cash outflow — total money leaving account = T + C |
| `ε` | Tolerance: `|T − VP × Q| ≤ max(0.005, 0.0001 × T)` |

Zero-row rule: if VP = 0 **and** T = 0 **and** C = 0 → classify `PENDING_CORPORATE_ACTION`, not `BUY`.

---

## Outcome tiers

| Tier | Symbol | Meaning |
|------|--------|---------|
| Blocking | ❌ BLOCK | Row rejected; import cannot proceed for this row |
| Warning | ⚠️ WARN | Row importable only after explicit user acknowledgement |
| Accepted | ✅ ACCEPT | Row imported silently |

A **commit** is refused if any BLOCK-tier row exists in the batch, regardless of other rows.  
A **partial import** commits only ACCEPT rows and skips WARN rows unless the user has acknowledged each WARN.

---

## Test matrix

### 1 — Delimiter & locale parsing

| TC | Año | Empresa | Fecha compra | Valor compra | Acciones | Total (€) | Comisión | Expected | Reason |
|----|-----|---------|-------------|-------------|---------|----------|---------|---------|--------|
| L-01 | 2024 | Acme Corp | 15/03/2024 | 12.50 | 100 | 1250.00 | 4.95 | ✅ ACCEPT | Canonical dot-decimal, slash date |
| L-02 | 2024 | Acme Corp | 15/03/2024 | 12,50 | 100 | 1.250,00 | 4,95 | ✅ ACCEPT | European comma-decimal, dot-thousand; parser must normalise |
| L-03 | 2024 | Acme Corp | 15/03/2024 | 12.50 | 100 | 1.250,00 | 4.95 | ❌ BLOCK | Mixed locale in same row (dot price, comma total) — ambiguous |
| L-04 | 2024 | Acme Corp | 2024-03-15 | 12.50 | 100 | 1250.00 | 4.95 | ✅ ACCEPT | ISO date |
| L-05 | 2024 | Acme Corp | 15-Mar-24 | 12.50 | 100 | 1250.00 | 4.95 | ⚠️ WARN | Ambiguous 2-digit year; user must confirm |
| L-06 | 2024 | Acme Corp | 03/15/2024 | 12.50 | 100 | 1250.00 | 4.95 | ❌ BLOCK | MM/DD/YYYY not declared; day=15 rules out day-first but importer must reject unless locale declared |
| L-07 | 2024 | Acme Corp | 15/03/2024 | 12.50 | 100 | 1250.00 | 4.95 | ✅ ACCEPT | Semicolon-delimited file (CSV dialect declared in header); same data as L-01 |
| L-08 | 2024 | Acme Corp | 15/03/2024 | 12.50 | 100 | 1250.00 | 4.95 | ❌ BLOCK | Tab-delimited file but importer expects comma/semicolon only; header column count mismatch |

### 2 — Unicode company names

| TC | Empresa (synthetic) | Expected | Reason |
|----|---------------------|---------|--------|
| U-01 | `Société Générale SA` | ✅ ACCEPT | Latin-extended accents |
| U-02 | `Müller & Söhne AG` | ✅ ACCEPT | German umlauts |
| U-03 | `日立製作所` | ✅ ACCEPT | CJK characters in UTF-8 |
| U-04 | `Аэрофлот ПАО` | ✅ ACCEPT | Cyrillic |
| U-05 | `شركة أرامكو` | ✅ ACCEPT | Arabic RTL string stored as column value |
| U-06 | `Acme\x00Corp` | ❌ BLOCK | Null byte in name — likely encoding error |
| U-07 | ` ` (only whitespace) | ❌ BLOCK | Empty company name |

### 3 — Price × Quantity = Total tolerance

Full rows use synthetic ticker `TestCo` and date `2025-06-01`.

| TC | VP | Q | T | ε check | Expected | Reason |
|----|-----|---|---|---------|---------|--------|
| P-01 | 10.000000 | 3.000000 | 30.00 | 0.00 | ✅ ACCEPT | Exact |
| P-02 | 10.123456 | 7.500000 | 75.93 | 0.003 < 0.005 | ✅ ACCEPT | Within half-cent tolerance |
| P-03 | 10.123456 | 7.500000 | 75.85 | 0.076 > 0.005 | ❌ BLOCK | Exceeds absolute ε; T inconsistent |
| P-04 | 0.001234 | 10000.000000 | 12.34 | 0.00 | ✅ ACCEPT | Penny stock, high Q, check relative ε |
| P-05 | 250.000000 | 1.333333 | 333.33 | 0.0025 < max(0.005, 0.0333) | ✅ ACCEPT | High-value fractional within relative ε |
| P-06 | 250.000000 | 1.333333 | 333.00 | 0.3325 > 0.0333 | ❌ BLOCK | Exceeds relative ε |
| P-07 | 10.00 | 100.00 | 0.00 | ≠ 1000 | ❌ BLOCK | T=0 but VP and Q both non-zero — zero-row rule does not apply |

### 4 — Total vs cash outflow

| TC | VP | Q | T | C | T_check | CF_check | Expected | Reason |
|----|-----|---|---|---|---------|---------|---------|--------|
| CF-01 | 10.00 | 100 | 1000.00 | 5.00 | ✅ | CF=1005.00 ✅ | ✅ ACCEPT | Textbook |
| CF-02 | 10.00 | 100 | 1005.00 | 5.00 | T includes commission | CF=1010 | ❌ BLOCK | T must exclude commission per contract |
| CF-03 | 10.00 | 100 | 1000.00 | 0.00 | ✅ | CF=1000 ✅ | ✅ ACCEPT | Zero commission (e.g. commission-free broker) |
| CF-04 | 10.00 | 100 | 1000.00 | -5.00 | ✅ | CF=995 | ❌ BLOCK | Negative commission not valid in purchase context |
| CF-05 | 10.00 | 100 | 1000.00 | | missing | | ❌ BLOCK | Commission column absent in non-header row |

### 5 — Fractional shares & decimal precision

| TC | VP | Q | T | C | Expected | Reason |
|----|-----|---|---|---|---------|--------|
| FQ-01 | 150.00 | 0.666667 | 100.00 | 1.00 | ✅ ACCEPT | Fractional share, ε=0.00005 < 0.01 |
| FQ-02 | 150.00 | 0.1 | 15.00 | 0.50 | ✅ ACCEPT | 1-decimal fractional |
| FQ-03 | 150.00 | 0.000001 | 0.000150 | 0.00 | ✅ ACCEPT | Micro-fraction; T stored at max precision |
| FQ-04 | 150.00 | 0.00 | 0.00 | 0.00 | ❌ BLOCK | Q=0 with non-zero VP (not a zero-row) |
| FQ-05 | 0.00 | 5.00 | 0.00 | 0.00 | ✅ ACCEPT → `PENDING_CORPORATE_ACTION` | Zero-row rule: VP=T=C=0, Q>0 |
| FQ-06 | 0.00 | 0.00 | 0.00 | 0.00 | ❌ BLOCK | All zeros including Q — meaningless row |

### 6 — Zero-cost rows and scrip/dividend pairing

Zero-row rule: VP=0, T=0, C=0, Q>0 → status = `PENDING_CORPORATE_ACTION`.

| TC | Scenario | Expected | Notes |
|----|----------|---------|-------|
| Z-01 | Zero row exists; no pending rights event for that security | ✅ ACCEPT as PENDING_CORPORATE_ACTION | No auto-pair; user must reconcile |
| Z-02 | Zero row exists; matching pending rights event found (same security, date within ±5 days) | ✅ ACCEPT as PENDING_CORPORATE_ACTION + ⚠️ WARN suggestion | Importer surfaces suggestion only — user must confirm pair |
| Z-03 | Zero row exists; multiple rights events in range | ✅ ACCEPT as PENDING_CORPORATE_ACTION + ⚠️ WARN suggestion (ambiguous) | List all candidates; user picks |
| Z-04 | Zero row VP=0, T=0, C=5.00 | ❌ BLOCK | Commission without cost basis is invalid |
| Z-05 | Zero row imported a second time (exact duplicate) | ❌ BLOCK | Duplicate detection applies to corporate-action rows too |

### 7 — Paid top-up vs ordinary buy ambiguity

Adjacent rows share company and date but differ in price. Could be a split purchase or a different lot.

| TC | Row A | Row B | Expected | Reason |
|----|-------|-------|---------|--------|
| TA-01 | 2025-01-10, WidgetCo, 20.00, 50, 1000.00, 2.00 | 2025-01-10, WidgetCo, 20.05, 30, 601.50, 1.50 | ⚠️ WARN | Same day, same co, prices differ by <1%; likely fill-split — user confirms |
| TA-02 | 2025-01-10, WidgetCo, 20.00, 50, 1000.00, 2.00 | 2025-01-10, WidgetCo, 21.50, 30, 645.00, 1.50 | ✅ ACCEPT (both) | >5% price spread — distinct fills or top-up at different price; no ambiguity warning needed |
| TA-03 | 2025-01-10, WidgetCo, 20.00, 50, 1000.00, 2.00 | 2025-01-10, WidgetCo, 20.00, 50, 1000.00, 2.00 | ❌ BLOCK | Exact duplicate — see TC DUP-01 |

Price-proximity threshold for split-buy warning: `|VP_A − VP_B| / VP_A < 0.01` (1%).

### 8 — Exact duplicates vs legitimate same-day split buys

| TC | Description | Expected | Reason |
|----|-------------|---------|--------|
| DUP-01 | Two rows identical on all 7 columns within same batch | ❌ BLOCK | Exact duplicate |
| DUP-02 | Two rows identical on all 7 columns across two separate batches | ❌ BLOCK | Cross-batch duplicate detected at commit via idempotency key |
| DUP-03 | Same day, same co, same VP, different Q and T (split fill) | ⚠️ WARN | Likely split execution; user confirms both are intentional |
| DUP-04 | Same row re-submitted after a failed partial import | ✅ ACCEPT | Idempotency key unchanged; importer deduplicates gracefully |
| DUP-05 | DUP-04 but previously committed successfully | ❌ BLOCK | Already persisted; re-import rejected |

Idempotency key: `SHA-256(Año + Empresa_normalised + Fecha compra_iso + VP + Q + T + C)`.

### 9 — Year / date mismatch

| TC | Año | Fecha compra | Expected | Reason |
|----|-----|-------------|---------|--------|
| YM-01 | 2024 | 15/03/2024 | ✅ ACCEPT | Consistent |
| YM-02 | 2024 | 15/03/2025 | ❌ BLOCK | Year column contradicts date year |
| YM-03 | 2024 | 15/03/2023 | ❌ BLOCK | Same — year column must equal calendar year of Fecha compra |
| YM-04 | 2024 | 29/02/2024 | ✅ ACCEPT | 2024 is a leap year |
| YM-05 | 2023 | 29/02/2023 | ❌ BLOCK | 2023 is not a leap year — invalid date |
| YM-06 | 2024 | 31/04/2024 | ❌ BLOCK | April has 30 days |

### 10 — Negative/zero quantity and invalid values

| TC | Field | Value | Expected | Reason |
|----|-------|-------|---------|--------|
| NV-01 | Acciones | -10 | ❌ BLOCK | Negative quantity in purchase context |
| NV-02 | Valor compra | -5.00 | ❌ BLOCK | Negative unit price |
| NV-03 | Total (€) | -500.00 | ❌ BLOCK | Negative total |
| NV-04 | Año | 1899 | ❌ BLOCK | Year before stock markets existed (< 1900) |
| NV-05 | Año | 2099 | ❌ BLOCK | Future year beyond reasonable horizon (> current year + 1) |
| NV-06 | Valor compra | "N/A" | ❌ BLOCK | Non-numeric |
| NV-07 | Acciones | "" | ❌ BLOCK | Empty required field |
| NV-08 | Comisión | "free" | ❌ BLOCK | Non-numeric; zero should be represented as 0 or 0.00 |

### 11 — Security resolution *(BLOCKING — amended 2026-09-05)*

Resolution is backed by the unified security catalogue. A security may exist with zero shares (watchlist-only); this satisfies resolution. See the **Security resolution — advanced test cases** section for full coverage of collisions, aliases, AI candidates, and retry flows.

| Tier | Condition | Commit outcome |
|------|-----------|---------------|
| **T1** | Exact canonical `security_id` or name match | ✅ ACCEPT auto |
| **T2** | Exact durable alias (case-insensitive, registered in catalogue) | ✅ ACCEPT auto |
| **T3** | AI / fuzzy suggestion — one or more candidates | ❌ BLOCK until user confirms exactly one |
| **T4** | No candidate, or user rejected all suggestions | ❌ BLOCK until security created in catalogue |

| TC | Empresa | Resolution | Expected | Reason |
|----|---------|-----------|---------|--------|
| SEC-01 | `TestCo Inc` | Exact canonical match | ✅ ACCEPT | T1 auto |
| SEC-02 | `TestCo` | Durable alias `TestCo → TCO·SYNTH` | ✅ ACCEPT | T2 auto; case-insensitive |
| SEC-03 | `TestCo Inc.` | AI suggests `TestCo Inc` (score 0.97) | ❌ BLOCK | T3; punctuation variant — explicit confirmation required |
| SEC-04 | `TESTCO INC` | AI suggests `TestCo Inc` (score 0.95) | ❌ BLOCK | T3; case variant — explicit confirmation required |
| SEC-05 | SEC-03 / SEC-04 after user confirms AI suggestion | `security_id` linked | ✅ ACCEPT on commit | Block lifts only after per-row confirmation |
| SEC-06 | `XYZ Ventures 1234` | No candidates | — | ❌ BLOCK T4; create security first |
| SEC-07 | `XYZ Ventures 1234` | User rejects all AI suggestions | Rejected | ❌ BLOCK T4 |
| SEC-08 | SEC-06 after user creates `XYZ Ventures 1234` in catalogue | `security_id` now exists | ✅ ACCEPT on idempotent re-upload | |

### 12 — Optional account field

The schema has 7 mandatory columns. If an 8th `Cuenta` (account) column is present:

| TC | Account field present | Value | Expected | Reason |
|----|-----------------------|-------|---------|--------|
| AC-01 | No | — | ✅ ACCEPT | Optional field absent; defaults to primary account |
| AC-02 | Yes | `"Cuenta A"` — known account | ✅ ACCEPT | Valid optional |
| AC-03 | Yes | `"Cuenta Z"` — unknown account | ✅ ACCEPT | Unknown account defaults to primary; no warning |
| AC-04 | Yes | `""` | ✅ ACCEPT | Empty optional defaults to primary account; no warning |

### 13 — Batch idempotency / cross-batch duplicates

| TC | Scenario | Expected | Reason |
|----|----------|---------|--------|
| BI-01 | Batch uploaded twice without changes | ❌ BLOCK all rows on second upload | All idempotency keys already committed |
| BI-02 | Batch A uploaded; Batch B contains 1 new row + 3 rows from Batch A | ❌ BLOCK 3 duplicate rows; ✅ ACCEPT 1 new row | Partial cross-batch dedup |
| BI-03 | Same row in same batch twice (intra-batch duplicate) | ❌ BLOCK both instances | Intra-batch dedup before commit |
| BI-04 | Batch uploaded; previous commit rolled back by user; same batch re-uploaded | ✅ ACCEPT | Rollback clears idempotency keys; re-import valid |
| BI-05 | Batch B has a zero-row (PENDING_CORPORATE_ACTION) that matches a zero-row already in Batch A | ❌ BLOCK | Dedup applies to corporate-action rows |

### 14 — Partial import and holdings impact

| TC | Scenario | Expected | Holdings impact |
|----|----------|---------|-----------------|
| PI-01 | Batch: 5 ACCEPT + 0 WARN + 0 BLOCK → commit | All 5 imported | Holdings updated atomically; subtotals reflect new lots |
| PI-02 | Batch: 5 ACCEPT + 2 WARN (acknowledged) + 0 BLOCK → commit | All 7 imported | Same |
| PI-03 | Batch: 5 ACCEPT + 2 WARN (NOT acknowledged) + 0 BLOCK → commit | 5 imported; 2 skipped with report | Holdings reflect only 5 rows |
| PI-04 | Batch: 5 ACCEPT + 0 WARN + 1 BLOCK → commit attempted | ❌ Entire commit refused | Holdings unchanged |
| PI-05 | PI-04 after user fixes BLOCK row externally → re-upload | ✅ ACCEPT all 6 | Full holdings update |
| PI-06 | Commit succeeds for 5 rows; DB write fails on row 3 | Full rollback → 0 rows committed | Atomicity: all-or-nothing per commit |

---

## Preview / Commit / Reconciliation acceptance criteria

### Preview phase

1. Parse validation (columns, types, locale normalisation) runs on every row before any DB write.
2. Idempotency key computed and checked against committed records; duplicates surfaced immediately.
3. Security name resolution attempted using tier model (T1 exact → T2 alias → T3 AI suggestion → T4 no match); unresolved names shown with BLOCK badge and grouped by company name with row indices.
4. Zero-row candidates flagged as `PENDING_CORPORATE_ACTION`; rights-event suggestions listed (non-binding).
5. Price × quantity ε check performed; violations shown with computed delta.
6. Year/date consistency verified.
7. Split-buy ambiguity (TA-01 pattern) highlighted with a WARN badge.
8. Preview report shows per-row outcome (BLOCK / WARN / ACCEPT) and a batch summary count.
9. **No DB writes occur during preview.**

### Commit phase

1. **Refused if any BLOCK row is present** — user must fix source CSV and re-upload.
2. WARN rows require explicit per-row acknowledgement checkbox before commit button activates.
3. Unacknowledged WARN rows are excluded from commit (partial import); user notified of exclusion count.
4. Commit is a single DB transaction: all accepted rows commit atomically or none do.
5. Idempotency keys written to a `purchase_import_keys` store at commit time.
6. Holdings lots updated in the same transaction as import rows (no intermediate inconsistent state).
7. Post-commit report lists: rows imported, rows excluded (with reasons), new lot IDs, updated security totals.

### Reconciliation phase

1. `PENDING_CORPORATE_ACTION` rows appear in a dedicated reconciliation queue, not in the BUY ledger.
2. User may link a pending row to a rights/scrip event; link is a suggestion only until confirmed.
3. Confirmed link updates the row status to `CORPORATE_ACTION_CONFIRMED` and posts to the event log.
4. Unlinked corporate-action rows remain in the queue indefinitely until acted upon.
5. Holdings query must exclude `PENDING_CORPORATE_ACTION` rows from cost-basis and average-price calculations until confirmed.
6. A reconciliation report is available at any time showing: pending count, suggested links, oldest unresolved date.

---

## Edge-case combinations (compound TCs)

| TC | Combination | Expected |
|----|-------------|---------|
| XC-01 | Zero-row (Z-01) + unresolved security (T3/T4) | ❌ BLOCK takes precedence; zero-row classification is secondary |
| XC-02 | Exact duplicate (DUP-01) + year mismatch (YM-02) | ❌ BLOCK; both violations reported |
| XC-03 | Fractional share (FQ-01) + European locale (L-02) + Unicode name (U-03) | ✅ ACCEPT after normalisation |
| XC-04 | Split-buy pair (TA-01) + one row is cross-batch duplicate (BI-02) | ❌ BLOCK duplicate row; ⚠️ WARN remaining row for split-buy ambiguity |
| XC-05 | Valid batch PI-01 + one PENDING_CORPORATE_ACTION row with matching rights event (Z-02) | Commit 5 BUY rows; queue 1 PENDING with suggestion |

---

---

## Security resolution — advanced test cases
*(Amendment 2026-09-05T17:12:18+02:00 — applies to BUY, SELL, and DIVIDEND imports)*

No row of any type may commit without a confirmed canonical `security_id` from the unified security catalogue. A security may exist in the catalogue with zero shares (watchlist-only entry); this satisfies resolution.

### Ticker collision across exchanges

Synthetic ticker `SYN` exists on both `SYNTH_E1` and `SYNTH_E2`.

| TC | Empresa | Ticker hint | Expected | Reason |
|----|---------|------------|---------|--------|
| COL-01 | `SynthEx` | None | ❌ BLOCK | Name resolves equally to SYN·E1 and SYN·E2; ambiguous |
| COL-02 | `SynthEx` | `SYN:SYNTH_E1` (fully qualified) | ✅ ACCEPT | Exchange qualifier resolves collision; T1 |
| COL-03 | `SynthEx` | `SYN` (unqualified bare ticker) | ❌ BLOCK | Ambiguity inherited; T3 at best |
| COL-04 | `SynthEx (E1)` | None | ✅ ACCEPT | Durable alias `SynthEx (E1) → SYN·E1` registered; T2 auto |

### Share class collisions

Synthetic `DualCo` has two classes: `DualCo A (DCO.A)` and `DualCo B (DCO.B)`.

| TC | Empresa | Resolution | Expected | Reason |
|----|---------|-----------|---------|--------|
| CLS-01 | `DualCo` | Matches DCO.A and DCO.B equally | ❌ BLOCK | Class unspecified; both are candidates |
| CLS-02 | `DualCo A` | Durable alias → DCO.A | ✅ ACCEPT | T2 auto |
| CLS-03 | `DualCo Class A` | AI suggests DCO.A (score 0.92) | ❌ BLOCK | T3; variant wording needs confirmation |
| CLS-04 | `DualCo Pref` | No match | ❌ BLOCK | T4; preferred class not in catalogue |

### Unicode and name variant handling

| TC | Empresa in CSV | Alias / match | Expected | Reason |
|----|---------------|--------------|---------|--------|
| UNI-01 | `Societe Generale` | Durable alias (no-accent form) → `SGE·SYNTH` | ✅ ACCEPT | T2 |
| UNI-02 | `Société Générale` | Exact canonical name | ✅ ACCEPT | T1 |
| UNI-03 | `SOCIETE GENERALE` | Alias lookup case-insensitive → `SGE·SYNTH` | ✅ ACCEPT | T2 |
| UNI-04 | `Societe Gen.` | AI suggests `SGE·SYNTH` (0.88) | ❌ BLOCK | T3; abbreviated punctuation form |
| UNI-05 | `日立製作所` | Exact canonical name (CJK stored in catalogue) | ✅ ACCEPT | T1 |
| UNI-06 | `Hitachi` | Durable alias `Hitachi → HIT·SYNTH` | ✅ ACCEPT | T2 |
| UNI-07 | `Hitach` (truncated typo) | AI suggests `HIT·SYNTH` (0.87) | ❌ BLOCK | T3 |

### Ambiguous AI candidates

| TC | Empresa | AI candidates | User action | Expected |
|----|---------|--------------|------------|---------|
| AMB-01 | `Synth Beverages` | Single (score 0.89) | Confirms | ✅ ACCEPT |
| AMB-02 | `Synth Beverages` | Single (score 0.89) | Rejects | ❌ BLOCK T4 |
| AMB-03 | `Synth Beverages` | Two (scores 0.82, 0.80) | Picks one | ✅ ACCEPT |
| AMB-04 | `Synth Beverages` | Two (scores 0.82, 0.80) | Picks neither | ❌ BLOCK T4 |
| AMB-05 | `Synth Beverages` | None | — | ❌ BLOCK T4 |

### Create-security-then-resume batch

| Step | Action | Expected |
|------|--------|---------|
| 1 | Upload batch: 5 rows resolved (T1/T2), 3 rows BLOCK on `NewCo Ltd` | Preview: 5 ACCEPT, 3 BLOCK; commit refused |
| 2 | User creates `NewCo Ltd → NCL·SYNTH` in security catalogue | Catalogue updated |
| 3 | Re-upload identical batch (idempotent retry) | 3 rows now resolve T1; idempotency keys unchanged; no false duplicate |
| 4 | Commit | All 8 rows imported atomically |

### Grouped affected row counts

Preview must aggregate BLOCK rows by unresolved company and display row indices:

```
BLOCKED — security unresolved:
  • "NewCo Ltd"      3 rows  (rows 4, 7, 12)
  • "OtherUnk Corp"  1 row   (row 9)
  Total blocked: 4. Commit refused.
```

| TC | Scenario | Expected |
|----|----------|---------|
| GRP-01 | 3 rows share same unresolved company | Grouped under one entry with row numbers |
| GRP-02 | 2 different unresolved companies, 2 rows each | Two groups shown |
| GRP-03 | All rows resolved | No BLOCK section displayed |
| GRP-04 | 1 resolved + 1 BLOCK | Both shown; commit refused |

### Partial import of only resolved companies

| TC | Scenario | Expected |
|----|----------|---------|
| PI-S-01 | 3 resolved + 2 BLOCK — user attempts commit | ❌ Refused; BLOCK rows cannot be silently skipped |
| PI-S-02 | 3 resolved + 2 BLOCK — user removes BLOCK rows from CSV, re-uploads | ✅ 3 imported; excluded rows reported |
| PI-S-03 | 3 resolved + 2 WARN (acknowledged) | ✅ 5 imported |
| PI-S-04 | 3 resolved + 2 WARN (not acknowledged) | ✅ 3 imported; 2 excluded with report |

### Idempotent retry after security mapping

| TC | Scenario | Expected |
|----|----------|---------|
| IDM-01 | Batch BLOCKed on `UnkCo`; user maps `UnkCo → UNK·SYNTH`; re-uploads identical batch | Previously-BLOCK rows now ACCEPT; idempotency keys unchanged |
| IDM-02 | Same as IDM-01 but user also edits an unrelated field | New idempotency key; treated as a new distinct row |
| IDM-03 | IDM-01 committed; same batch re-uploaded | ❌ BLOCK all (already committed) |

---

_No real user financial data is contained in this document. All values, company names, and dates are synthetic._
