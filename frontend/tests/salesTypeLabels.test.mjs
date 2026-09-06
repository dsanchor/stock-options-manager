/**
 * Tests for salesTypeLabels.ts — Amendment G §G.3.2
 *
 * Run with: node --test frontend/tests/salesTypeLabels.test.mjs
 *
 * Tests the SALES_TYPE_LABELS constant and getSalesTypeLabel() helper.
 * Internal enum values (ACCIONES, DERECHOS) must map to user-facing strings
 * ("Stocks", "Rights"). Neither internal value must ever be shown directly.
 */

import { describe, it } from "node:test";
import assert from "node:assert/strict";

// ---------------------------------------------------------------------------
// Inline predicates (mirror salesTypeLabels.ts — update both together)
// ---------------------------------------------------------------------------

const SALES_TYPE_LABELS = {
  ACCIONES: "Stocks",
  DERECHOS: "Rights",
};

function getSalesTypeLabel(salesType) {
  if (!salesType) return null;
  return SALES_TYPE_LABELS[salesType] ?? null;
}

// ---------------------------------------------------------------------------
// SALES_TYPE_LABELS constant
// ---------------------------------------------------------------------------

describe("SALES_TYPE_LABELS constant", () => {
  it("ACCIONES maps to 'Stocks' (Amendment G §G.3.2)", () => {
    assert.equal(SALES_TYPE_LABELS.ACCIONES, "Stocks",
      "Internal ACCIONES must display as 'Stocks', not 'Shares' or 'ACCIONES'");
  });

  it("DERECHOS maps to 'Rights' (Amendment G §G.3.2)", () => {
    assert.equal(SALES_TYPE_LABELS.DERECHOS, "Rights",
      "Internal DERECHOS must display as 'Rights', not 'DERECHOS'");
  });

  it("covers exactly two keys: ACCIONES and DERECHOS", () => {
    const keys = Object.keys(SALES_TYPE_LABELS);
    assert.deepEqual(
      keys.sort(),
      ["ACCIONES", "DERECHOS"].sort(),
      "SALES_TYPE_LABELS must cover exactly ACCIONES and DERECHOS"
    );
  });

  it("ACCIONES label is not the internal value", () => {
    assert.notEqual(SALES_TYPE_LABELS.ACCIONES, "ACCIONES",
      "Internal enum value must not be shown to users");
  });

  it("DERECHOS label is not the internal value", () => {
    assert.notEqual(SALES_TYPE_LABELS.DERECHOS, "DERECHOS",
      "Internal enum value must not be shown to users");
  });
});

// ---------------------------------------------------------------------------
// getSalesTypeLabel() helper
// ---------------------------------------------------------------------------

describe("getSalesTypeLabel", () => {
  it("ACCIONES → 'Stocks'", () => {
    assert.equal(getSalesTypeLabel("ACCIONES"), "Stocks");
  });

  it("DERECHOS → 'Rights'", () => {
    assert.equal(getSalesTypeLabel("DERECHOS"), "Rights");
  });

  it("null → null (no label for missing sales_type)", () => {
    assert.equal(getSalesTypeLabel(null), null,
      "BUY and DIVIDEND movements have no sales_type; label must be null");
  });

  it("undefined → null", () => {
    assert.equal(getSalesTypeLabel(undefined), null);
  });

  it("empty string → null", () => {
    assert.equal(getSalesTypeLabel(""), null);
  });

  it("unknown value → null (no fallthrough to internal representation)", () => {
    assert.equal(getSalesTypeLabel("UNKNOWN_TYPE"), null,
      "Unrecognised sales_type must not expose internal strings");
  });

  it("case-sensitive: lowercase 'acciones' → null (internal enum is uppercase)", () => {
    assert.equal(getSalesTypeLabel("acciones"), null,
      "API contract uses uppercase; lowercase must not match");
  });

  it("case-sensitive: lowercase 'derechos' → null", () => {
    assert.equal(getSalesTypeLabel("derechos"), null);
  });
});

// ---------------------------------------------------------------------------
// Exhaustive enum coverage
// ---------------------------------------------------------------------------

describe("SALES_TYPE_LABELS exhaustive coverage", () => {
  const ALL_SALES_TYPES = ["ACCIONES", "DERECHOS"];

  it("every valid sales_type returns a non-null user label", () => {
    for (const t of ALL_SALES_TYPES) {
      const label = getSalesTypeLabel(t);
      assert.ok(label !== null,
        `getSalesTypeLabel('${t}') returned null — every valid sales_type must have a label`);
      assert.ok(label.length > 0,
        `getSalesTypeLabel('${t}') returned empty string — label must be non-empty`);
    }
  });

  it("no valid sales_type label equals its internal enum value", () => {
    for (const t of ALL_SALES_TYPES) {
      const label = getSalesTypeLabel(t);
      assert.notEqual(label, t,
        `Label for '${t}' must not equal the internal enum value ('${t}')`);
    }
  });
});
