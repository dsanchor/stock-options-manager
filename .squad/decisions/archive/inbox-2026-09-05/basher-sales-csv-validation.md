# Sales CSV Import — Synthetic Test Matrix & Acceptance Criteria

**Author:** Basher (Tester/Reviewer)  
**Date:** 2026-09-05  
**Scope:** 6-column sales importer: `Año | Empresa | Fecha venta | Acciones | Comisión | Total Venta`  
**Directive:** 2026-09-05T17:05:23+02:00 — preserve gross proceeds as source truth; derive net cash and unit price; reconcile with lot history post-import.  
**Constraint:** All fixtures are fully synthetic. No real user financial rows reproduced.

> **Amendment 2026-09-05T17:12:18+02:00:** UNRESOLVED_SECURITY is now **BLOCKING** for all import types. No BUY, SELL, or DIVIDEND row may commit without a confirmed canonical `security_id`. Exact durable aliases auto-resolve (Tier 2); AI/fuzzy suggestions are advisory and require explicit per-row confirmation before the block lifts. Account field remains optional — no warning generated.

---

## Semantic contract

| Symbol | Column | Meaning |
|--------|--------|---------|
| `TV` | Total Venta | Gross sale proceeds (source field; authoritative) |
| `Q` | Acciones | Quantity sold (may be fractional) |
| `C` | Comisión | Broker fee on the sale |
| `NC` | — | **Derived:** Net cash received = TV − C |
| `UP` | — | **Derived (informational):** Unit price = TV / Q; never written to source |
| `RG` | — | **Derived (deferred):** Realized gain = TV − C − cost_basis; requires lot history + cost method |

### Derivation rules

1. `NC` = TV − C. If NC < 0, that is economically unusual but not impossible → ⚠️ WARN, not BLOCK.  
2. `UP` is computed for display only; it must never be back-propagated to the CSV or treated as a source price.  
3. `RG` is `UNAVAILABLE` until: (a) matching purchase lots exist, and (b) a cost method (FIFO / LIFO / average / specific lot) is declared. Absence of `RG` is not an error.  
4. Inventory position at sale date = sum(Q_purchased up to Fecha venta) − sum(Q_sold up to Fecha venta, exclusive of current row). Shortfall → ⚠️ WARN (non-blocking).

---

## Outcome tiers

| Tier | Symbol | Meaning |
|------|--------|---------|
| Blocking | ❌ BLOCK | Row rejected; commit refused while any BLOCK is present |
| Warning | ⚠️ WARN | Importable after explicit per-row user acknowledgement |
| Accepted | ✅ ACCEPT | Imported silently |

A commit is refused if **any** BLOCK row is present in the batch.  
Unacknowledged WARN rows are excluded from commit (partial import).

---

## Test matrix

### 1 — Spanish locale parsing

All rows use synthetic company `SynthCo` and a plausible sale date.

| TC | Delimiter | Decimal sep | Date format | Example row fragment | Expected | Reason |
|----|-----------|------------|------------|---------------------|---------|--------|
| SP-01 | Comma `,` | Period `.` | DD/MM/YYYY | `2024,SynthCo,15/03/2024,100,4.95,1250.00` | ✅ ACCEPT | Canonical |
| SP-02 | Semicolon `;` | Comma `,` | DD/MM/YYYY | `2024;SynthCo;15/03/2024;100;4,95;1.250,00` | ✅ ACCEPT | European locale; dot-thousand removed, comma-decimal normalised |
| SP-03 | Semicolon `;` | Mixed per row | DD/MM/YYYY | `2024;SynthCo;15/03/2024;100;4.95;1.250,00` | ❌ BLOCK | Intra-row decimal inconsistency (dot on C, comma on TV) |
| SP-04 | Comma `,` | Period `.` | YYYY-MM-DD | `2024,SynthCo,2024-03-15,100,4.95,1250.00` | ✅ ACCEPT | ISO date |
| SP-05 | Comma `,` | Period `.` | 15-Mar-24 | `2024,SynthCo,15-Mar-24,100,4.95,1250.00` | ⚠️ WARN | Ambiguous 2-digit year |
| SP-06 | Comma `,` | Period `.` | MM/DD/YYYY | `2024,SynthCo,03/15/2024,100,4.95,1250.00` | ❌ BLOCK | MM/DD not declared; importer expects DD/MM or ISO |
| SP-07 | Tab `\t` | Period `.` | DD/MM/YYYY | tab-separated row | ❌ BLOCK | Tab dialect not supported; column count mismatch |

### 2 — Gross proceeds preserved and net cash

| TC | TV | C | NC (expected) | Expected | Reason |
|----|----|---|--------------|---------|--------|
| GN-01 | 1250.00 | 4.95 | 1245.05 | ✅ ACCEPT | Textbook |
| GN-02 | 1250.00 | 0.00 | 1250.00 | ✅ ACCEPT | Commission-free broker |
| GN-03 | 1250.00 | 1250.00 | 0.00 | ⚠️ WARN | NC = 0; all proceeds consumed by commission |
| GN-04 | 1250.00 | 1251.00 | -1.00 | ⚠️ WARN | Commission exceeds gross proceeds; NC < 0 (high min-fee, tiny lot) |
| GN-05 | 0.00 | 0.00 | 0.00 | ⚠️ WARN | Worthless security sale; legal but unusual — classify as ZERO_PROCEED_SALE for reconciliation |
| GN-06 | 0.00 | 5.00 | -5.00 | ❌ BLOCK | Zero proceeds with non-zero commission and NC < 0 — inconsistent (commission with no proceeds warrants rejection) |
| GN-07 | 1250.7654321 | 4.95 | 1245.8154321 | ✅ ACCEPT | High-precision TV preserved exactly; NC computed at same precision |

> **GN-04 note:** Commission > TV produces negative NC. Importer must surface this but must not refuse the row outright — minimum broker fees on very small lots can legally exceed proceeds. User acknowledges.

### 3 — Derived unit price provenance

| TC | TV | Q | UP (derived) | Rule | Expected |
|----|----|---|-------------|------|---------|
| UP-01 | 1250.00 | 100 | 12.50 | Informational | ✅ ACCEPT; UP shown in preview as derived field |
| UP-02 | 100.00 | 0.666667 | 149.9999... ≈ 150.00 | Rounded to 6 dp for display | ✅ ACCEPT |
| UP-03 | 1250.00 | 100 | — | UP not stored | Persisted row must not contain a `unit_price` source column |
| UP-04 | 75.93 | 7.5 | 10.124 | Display-only | ✅ ACCEPT; UP never used to recompute TV |
| UP-05 | TV re-derived from UP × Q | — | — | Anti-pattern | ❌ BLOCK if any pipeline step reconstructs TV from a stored UP — regression guard |

### 4 — Fractional quantities and decimal precision

| TC | Q | TV | C | Expected | Reason |
|----|---|-----|---|---------|--------|
| FQ-01 | 0.666667 | 100.00 | 1.00 | ✅ ACCEPT | Fractional share |
| FQ-02 | 0.1 | 15.00 | 0.50 | ✅ ACCEPT | 1-decimal fractional |
| FQ-03 | 0.000001 | 0.000012 | 0.00 | ✅ ACCEPT | Micro-fraction; TV and NC stored at max precision |
| FQ-04 | 1.333333 | 333.33 | 2.00 | ✅ ACCEPT | UP derived = 249.997... — acceptable rounding |
| FQ-05 | 3.000000 | 450.00 | 3.00 | ✅ ACCEPT | Integer-valued Q stored as decimal |

### 5 — Zero and negative values

| TC | Field | Value | Expected | Reason |
|----|-------|-------|---------|--------|
| ZN-01 | Acciones | 0 | ❌ BLOCK | Zero quantity meaningless in sale context |
| ZN-02 | Acciones | -10 | ❌ BLOCK | Negative quantity invalid for sales |
| ZN-03 | Total Venta | -500.00 | ❌ BLOCK | Negative gross proceeds invalid |
| ZN-04 | Comisión | -5.00 | ❌ BLOCK | Negative commission invalid |
| ZN-05 | Año | 1899 | ❌ BLOCK | Year < 1900 |
| ZN-06 | Año | 2099 | ❌ BLOCK | Future year > current + 1 |
| ZN-07 | Acciones | `"N/A"` | ❌ BLOCK | Non-numeric |
| ZN-08 | Total Venta | `""` | ❌ BLOCK | Required field empty |
| ZN-09 | Comisión | `""` | ❌ BLOCK | Required field empty (use 0.00 for zero) |
| ZN-10 | Total Venta | 0.00 (Q > 0, C = 0) | ⚠️ WARN | Worthless-security sale; see GN-05 |

### 6 — Commission > total sale

| TC | TV | C | NC | Expected | Reason |
|----|----|---|-----|---------|--------|
| CT-01 | 10.00 | 9.99 | 0.01 | ✅ ACCEPT | C just under TV |
| CT-02 | 10.00 | 10.00 | 0.00 | ⚠️ WARN | C = TV; NC = 0 |
| CT-03 | 10.00 | 10.01 | -0.01 | ⚠️ WARN | C slightly > TV; economic loss but plausible minimum-fee scenario |
| CT-04 | 10.00 | 100.00 | -90.00 | ⚠️ WARN | Extreme commission; importer WARNS with the NC figure |

All CT-02 / CT-03 / CT-04 rows require explicit user acknowledgement before commit.

### 7 — Year / date mismatch

| TC | Año | Fecha venta | Expected | Reason |
|----|-----|------------|---------|--------|
| YM-01 | 2024 | 15/03/2024 | ✅ ACCEPT | Consistent |
| YM-02 | 2024 | 15/03/2025 | ❌ BLOCK | Year column contradicts date year |
| YM-03 | 2024 | 15/03/2023 | ❌ BLOCK | Same |
| YM-04 | 2024 | 29/02/2024 | ✅ ACCEPT | 2024 is a leap year |
| YM-05 | 2023 | 29/02/2023 | ❌ BLOCK | 2023 not a leap year |
| YM-06 | 2024 | 31/04/2024 | ❌ BLOCK | April has 30 days |
| YM-07 | 2024 | 15/03/2024 (future) | ❌ BLOCK | Sale date > today (at import time) |

### 8 — Security resolution *(BLOCKING — amended 2026-09-05)*

Resolution is backed by the unified security catalogue. A security may exist with zero shares (watchlist-only); this satisfies resolution. See **Security resolution — advanced test cases** for collisions, aliases, AI candidates, and retry flows.

| Tier | Condition | Commit outcome |
|------|-----------|---------------|
| **T1** | Exact canonical `security_id` or name match | ✅ ACCEPT auto |
| **T2** | Exact durable alias (case-insensitive, registered in catalogue) | ✅ ACCEPT auto |
| **T3** | AI / fuzzy suggestion — one or more candidates | ❌ BLOCK until user confirms exactly one |
| **T4** | No candidate, or user rejected all suggestions | ❌ BLOCK until security created in catalogue |

| TC | Empresa | Resolution | Expected | Reason |
|----|---------|-----------|---------|--------|
| SEC-01 | `SynthCo` | Exact canonical match | ✅ ACCEPT | T1 auto |
| SEC-02 | `SynthCo` (alias) | Durable alias `SynthCo → SCO·SYNTH` | ✅ ACCEPT | T2 auto |
| SEC-03 | `SynthCo.` | AI suggests `SynthCo` (score 0.97) | ❌ BLOCK | T3; punctuation variant — confirmation required |
| SEC-04 | `SYNTHCO` | AI suggests `SynthCo` (score 0.95) | ❌ BLOCK | T3; case variant — confirmation required |
| SEC-05 | SEC-03 / SEC-04 after user confirms suggestion | `security_id` linked | ✅ ACCEPT on commit | Block lifts only after per-row confirmation |
| SEC-06 | `Unknown Ventures 9999` | No candidates | — | ❌ BLOCK T4; create security first |
| SEC-07 | `Unknown Ventures 9999` | User rejects all AI suggestions | Rejected | ❌ BLOCK T4 |
| SEC-08 | SEC-06 after user creates `Unknown Ventures 9999` in catalogue | `security_id` now exists | ✅ ACCEPT on idempotent re-upload | |

### 9 — Optional account field

If an 8th `Cuenta` column is present:

| TC | Account present | Value | Expected | Reason |
|----|----------------|-------|---------|--------|
| AC-01 | No | — | ✅ ACCEPT | Defaults to primary account |
| AC-02 | Yes | `"Cuenta A"` (known) | ✅ ACCEPT | |
| AC-03 | Yes | `"Cuenta Z"` (unknown) | ✅ ACCEPT | Unknown account defaults to primary; no warning |
| AC-04 | Yes | `""` | ✅ ACCEPT | Empty → defaults to primary; no warning |

### 10 — Idempotency and possible duplicates

Idempotency key: `SHA-256(Año + Empresa_normalised + Fecha_venta_iso + Q + TV + C)`

| TC | Description | Expected | Reason |
|----|-------------|---------|--------|
| ID-01 | Same batch uploaded twice | ❌ BLOCK all on second upload | Keys already committed |
| ID-02 | Batch B contains 1 new row + 2 rows from committed Batch A | ❌ BLOCK 2 rows; ✅ ACCEPT 1 new | Cross-batch dedup |
| ID-03 | Same row appears twice in same batch | ❌ BLOCK both instances | Intra-batch dedup |
| ID-04 | Row re-submitted after rollback | ✅ ACCEPT | Rollback cleared keys |
| ID-05 | Two sales: same company, same date, Q differs | ⚠️ WARN | Possible split sale — see OR-05; user confirms intent |
| ID-06 | Two sales: same company, same date, all fields identical | ❌ BLOCK | Exact duplicate regardless of split-sale pattern |

---

## Import order and ledger replay (critical section)

This section tests the inventory model and gain calculation under varying import sequences.  
Synthetic securities used: `ReplayCo` (purchases) and `OversellCo` (oversell scenario).  
All quantities and prices are illustrative and synthetic.

### Inventory model at sale time

```
inventory(security, date) =
    SUM(Q_purchased, dates ≤ date)
  − SUM(Q_sold, dates < date, committed rows)
  − Q_current_sale_row
```

If `inventory < 0` after including current row → ⚠️ WARN (non-blocking): **negative inventory at sale date**.

### OR-01 — Sale imported before corresponding purchases (non-blocking warning)

**Scenario:** Batch S1 imports a sale of 100 shares of `ReplayCo` on 2024-06-01.  
No purchase records exist for `ReplayCo` at import time.

| Step | Action | Inventory | Expected |
|------|--------|-----------|---------|
| 1 | Import Batch S1 (sale 100 shares, 2024-06-01) | −100 at 2024-06-01 | ⚠️ WARN: negative inventory; commit allowed after acknowledgement |
| 2 | Commit Batch S1 | Sale persisted | ✅ Committed; `RG = UNAVAILABLE` |
| 3 | Query realized gain | No cost basis | `RG = UNAVAILABLE` (not an error) |

> **Rule:** Negative-inventory WARN is non-blocking. The sale is real; the purchase history may simply be missing from the system yet.

### OR-02 — Later purchase import resolves the warning (ledger replay)

Continuing from OR-01:

| Step | Action | Inventory after replay | Expected |
|------|--------|----------------------|---------|
| 4 | Import Batch P1: purchase 100 shares of `ReplayCo` on 2024-01-15 | 100 − 100 = 0 at 2024-06-01 | ✅ Inventory warning clears on replay |
| 5 | Commit Batch P1 | Ledger replayed chronologically | ⚠️ WARN removed from sale row OR-01 |
| 6 | Declare cost method = FIFO | Cost basis now available | `RG` computed and shown |

**Replay rule:** After any purchase commit, the system replays the full chronological ledger for the affected security. Inventory warnings that are now resolved are cleared automatically; those that remain are re-surfaced.

### OR-03 — Genuine oversell remains warned

| Step | Action | Inventory | Expected |
|------|--------|-----------|---------|
| 1 | Purchase 50 shares `OversellCo` on 2024-01-10 (committed) | +50 | — |
| 2 | Import sale of 80 shares `OversellCo` on 2024-06-01 | 50 − 80 = −30 | ⚠️ WARN: negative inventory (−30 shares) |
| 3 | Commit sale (user acknowledges) | Sale persisted | ⚠️ WARN remains open |
| 4 | Import additional purchase of 20 shares `OversellCo` on 2024-03-01 | 70 − 80 = −10 | ⚠️ WARN updated (−10 shares); not resolved |
| 5 | Import additional purchase of 10 shares `OversellCo` on 2024-04-01 | 80 − 80 = 0 | ✅ Warning clears |

**Rule:** A negative-inventory WARN clears only when cumulative purchases on or before the sale date meet or exceed cumulative sales.

### OR-04 — Realized gain unavailable without cost basis and method

| TC | State | RG | Expected |
|----|-------|----|---------|
| RG-01 | Sale committed; no purchases for security | `UNAVAILABLE` | ✅ Normal; no error |
| RG-02 | Sale committed; purchases committed; no cost method declared | `UNAVAILABLE` | ✅ Normal; prompt user to declare method |
| RG-03 | Sale committed; purchases committed; cost method = FIFO declared | Computed deterministically | ✅ `RG` shown; stored on reconciliation record |
| RG-04 | Sale committed; purchases committed; cost method = LIFO declared | Computed deterministically (different value) | ✅ Method change triggers recomputation for all sales of this security |
| RG-05 | Sale committed; partial purchases (covers 60% of Q sold) | `PARTIAL` — gain on 60% computed; remainder `UNAVAILABLE` | ⚠️ WARN: partial cost basis; user informed |
| RG-06 | RG recomputed after additional purchase committed | Fully determined if now sufficient | WARN clears if coverage reaches 100% |

**Rule:** `RG` computation is deferred and stateless at import time. It becomes deterministic only when the full lot history and a declared method are present. The importer must never block on this condition.

### OR-05 — Same-day split sales remain legal

Two separate sales of the same security on the same date are legitimate (e.g. two broker fills, two accounts).

| TC | Row A | Row B | Expected | Reason |
|----|-------|-------|---------|--------|
| SS-01 | 2024-06-01, `SplitSaleCo`, Q=50, TV=625.00, C=2.00 | 2024-06-01, `SplitSaleCo`, Q=30, TV=378.00, C=1.50 | ⚠️ WARN + ✅ ACCEPT both | Prices differ < 1%; WARN for split ambiguity; user confirms both fills are intentional |
| SS-02 | Same as SS-01 but prices differ > 5% | ✅ ACCEPT both | Distinct fills at materially different prices; no ambiguity |
| SS-03 | SS-01 but one row is exact duplicate of the other | ❌ BLOCK duplicate; ✅ ACCEPT other | Dedup takes precedence |
| SS-04 | SS-01 and combined Q (80) > inventory at date | ⚠️ WARN (negative inventory) + ⚠️ WARN (split ambiguity) | Both WARN issued; user must acknowledge each |

Split-sale proximity threshold (warning trigger): `|UP_A − UP_B| / UP_A < 0.01` (1%), same company, same date.

---

## Batch idempotency / cross-batch interactions

| TC | Scenario | Expected |
|----|----------|---------|
| BI-01 | Sales batch uploaded twice unchanged | ❌ BLOCK all rows on second upload |
| BI-02 | Batch contains 1 new sale + 2 already-committed sales | ❌ BLOCK 2; ✅ ACCEPT 1 |
| BI-03 | Intra-batch duplicate sale rows | ❌ BLOCK both instances |
| BI-04 | Re-upload after user rollback | ✅ ACCEPT (keys cleared by rollback) |
| BI-05 | Sale batch committed; purchase batch committed for same security; ledger replay triggered | ✅ Replay runs automatically; any resolved inventory WARNs cleared |

---

## Compound edge cases

| TC | Combination | Expected |
|----|-------------|---------|
| XC-01 | Sale before purchases (OR-01) + unresolved security (T3/T4) | ❌ BLOCK takes precedence; inventory WARN is secondary |
| XC-02 | Exact duplicate (ID-06) + year mismatch (YM-02) | ❌ BLOCK; both violations reported |
| XC-03 | Fractional quantity (FQ-02) + European locale (SP-02) + Unicode company name | ✅ ACCEPT after normalisation |
| XC-04 | Commission > TV (CT-04) + sale before purchases (OR-01) | ⚠️ WARN (commission) + ⚠️ WARN (inventory); both require acknowledgement |
| XC-05 | Genuine oversell (OR-03) + same-day split sale (SS-01) | ⚠️ WARN (oversell) + ⚠️ WARN (split ambiguity); inventory calc uses combined Q of both rows |
| XC-06 | Zero-proceed sale (GN-05) + no prior purchases | `ZERO_PROCEED_SALE` status + `RG = UNAVAILABLE`; no BLOCK |
| XC-07 | Split sale (SS-01) + one row is cross-batch duplicate | ❌ BLOCK duplicate row; ⚠️ WARN remaining row for split ambiguity |

---

## Preview / Commit / Reconciliation acceptance criteria

### Preview phase

1. Parse and locale-normalise all rows; type-check every field.  
2. Compute and display `NC = TV − C` and derived `UP = TV / Q` (clearly labelled as derived).  
3. Check idempotency keys against committed records; surface duplicates immediately.  
4. Resolve security names using tier model (T1 exact → T2 alias → T3 AI suggestion → T4 no match); display BLOCK badge per unresolved row, grouped by company name with row indices.  
5. Evaluate inventory position at each sale date using all committed purchase and sale rows; surface negative-inventory WARNs.  
6. Flag same-day, same-security rows with price proximity < 1% as split-sale candidates (WARN).  
7. Validate year/date consistency.  
8. Identify commission > TV rows; compute NC and display.  
9. Show per-row outcome badge (BLOCK / WARN / ACCEPT) and batch summary count.  
10. Show `RG = UNAVAILABLE` with reason (missing lots / missing method) for every sale row; this is informational, not an error.  
11. **No DB writes during preview.**

### Commit phase

1. **Refused if any BLOCK row is present.**  
2. Each WARN row requires an explicit per-row acknowledgement before the commit button activates.  
3. Unacknowledged WARN rows excluded from commit (partial import); user notified of count and reasons.  
4. Commit is a single DB transaction: all accepted rows commit atomically or none do.  
5. Idempotency keys written to `sale_import_keys` store at commit time.  
6. Inventory model updated in the same transaction.  
7. **Ledger replay triggered** automatically for every security touched by the batch: chronological re-evaluation of all inventory WARNs for that security across all committed sale rows.  
8. Post-commit report: rows imported, rows excluded (with reasons), inventory WARNs cleared by replay, remaining open WARNs, `RG` status per row.

### Reconciliation / gain calculation phase

1. `RG` is a deferred field on every sale record; its initial state after commit is `UNAVAILABLE`.  
2. `RG` becomes computable when: (a) the security has sufficient purchase lots on or before the sale date, and (b) a cost method is declared for the security or portfolio.  
3. A cost-method declaration triggers recomputation of all `RG` values for the affected security; results are stored on sale records (not re-derived on every query).  
4. Partial cost basis (lots covering < 100% of Q sold) produces a `PARTIAL` `RG`: computed portion shown, uncovered portion marked `UNAVAILABLE`.  
5. A new purchase commit for a security triggers partial-to-full `RG` resolution if coverage reaches 100%.  
6. Holdings query must use `TV` (gross) and `NC = TV − C` (net) directly from source; it must never recompute them from the derived `UP`.  
7. Reconciliation report shows: open inventory WARNs, `RG UNAVAILABLE` count, `RG PARTIAL` count, securities awaiting cost-method declaration.

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
