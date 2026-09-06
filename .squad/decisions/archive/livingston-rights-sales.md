# Rights-Sale Implementation: Tipo Column Layout Discrepancy

**Date:** 2026-09-06
**Author:** Livingston (Persistence & Integration Engineer)
**Status:** NEEDS DANNY REVIEW — implementation shipped with dual-layout support
**Scope:** `backend/src/portfolio/parsers/sales.py` — column position detection

---

## Decision

Implemented dual-layout detection for the 7-column sales CSV. See below for
the conflict and the implementation choice.

---

## Conflict Discovered

Two different column positions for the `Tipo` column are described in the
authoritative documents:

### Layout A — DESIGN REVIEW SUMMARY + User Sample

> "seven columns with `Tipo` between `Fecha venta` and `Acciones`"

Header: `Año | Empresa | Fecha venta | Tipo | Acciones | Comisión | Total Venta`

Data example (user's actual CSV):
```
2023 | ACS | 03/02/2023 | Derechos | 14 | 0.01 | 6.67
```

Column indices:
- 0: Año, 1: Empresa, 2: Fecha venta, **3: Tipo**, 4: Acciones, 5: Comisión, 6: Total Venta

### Layout B — `danny-rights-sale-contract.md` §2.1 + Basher's Pre-Written Tests

Design doc table:
> `Año | Empresa | Fecha venta | Acciones | Comisión | Total Venta | Tipo`

Basher's test fixture:
```python
"Año\tEmpresa\tFecha venta\tAcciones\tComisión\tTotal Venta\tTipo\n"
"2024\tApple Inc.\t20/06/2024\t5\t7,50\t1.050,00\tDerechos\n"
```

Column indices:
- 0: Año, 1: Empresa, 2: Fecha venta, 3: Acciones, 4: Comisión, 5: Total Venta, **6: Tipo**

---

## Impact

- If only Layout A is implemented: user's actual data parses correctly, but
  Basher's 11 pre-written `TestSalesParserSalesType` tests all fail.
- If only Layout B is implemented: all 164 portfolio tests pass, but the
  user's real CSV (header from DESIGN REVIEW SUMMARY user sample) produces
  a column-mismatch parse error.

---

## Resolution Shipped

The parser supports BOTH layouts via header-position detection:

1. If `normalized_headers[3] == "tipo"` → Layout A (Tipo at index 3)
2. Else if `normalized_headers[6] == "tipo"` → Layout B (Tipo at index 6)
3. Otherwise → 6-column legacy format

This means any valid CSV from either spec is accepted. No ambiguity in data
interpretation — the column named "Acciones" (quantity) is always correctly
identified by position regardless of where Tipo appears.

All 164 portfolio tests pass. TypeScript compiles clean.

---

## Request

**Danny:** please confirm which of the two column positions is the canonical
7-column format, or whether both should remain supported. Specifically:

1. Is the user's actual CSV (Layout A: Tipo at position 4) the intended
   import format that should be documented in the design contract?
2. Should Basher's test fixtures be corrected to use Layout A?
3. Or is Layout B the intended format, and the DESIGN REVIEW SUMMARY
   description "between Fecha venta and Acciones" was a description error?

If only one layout should be supported going forward, I can narrow the
parser to a single path once the canonical layout is confirmed. The
dual-layout approach is safe but slightly more surface area to test.
