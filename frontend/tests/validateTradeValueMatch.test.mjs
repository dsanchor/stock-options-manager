/**
 * Tests for validateTradeValueMatch.ts — Amendment G §G.1.3 cross-validation.
 *
 * Run with: node --test frontend/tests/validateTradeValueMatch.test.mjs
 *
 * Inline predicate mirrors validateTradeValueMatch.ts exactly.
 * Any divergence is a defect.
 */

import { describe, it } from "node:test";
import assert from "node:assert/strict";

// ---------------------------------------------------------------------------
// Inline predicate (mirrors validateTradeValueMatch.ts — update both together)
// ---------------------------------------------------------------------------

const TOLERANCE = 0.01;

function validateTradeValueMatch(quantity, unitPrice, tradeValue) {
  const hasQty = quantity != null && isFinite(quantity);
  const hasPrice = unitPrice != null && isFinite(unitPrice);
  const hasTrade = tradeValue != null && isFinite(tradeValue);

  if (hasQty && hasPrice && !hasTrade) {
    return { valid: true, computedTradeValue: quantity * unitPrice };
  }
  if (hasQty && hasTrade && !hasPrice) {
    if (quantity > 0) {
      return { valid: true, computedUnitPrice: tradeValue / quantity };
    }
    return { valid: true };
  }
  if (hasQty && hasPrice && hasTrade) {
    const computed = quantity * unitPrice;
    const delta = Math.abs(computed - tradeValue);
    if (delta > TOLERANCE) {
      return {
        valid: false,
        error:
          `Trade value doesn't match quantity × price. ` +
          `Expected ≈${computed.toFixed(2)}, got ${tradeValue.toFixed(2)}. ` +
          `Correct one of them.`,
      };
    }
    return { valid: true };
  }
  if (hasTrade && !hasQty && !hasPrice) {
    return { valid: true };
  }
  return { valid: true };
}

// ---------------------------------------------------------------------------
// Rule 1: qty + price → auto-compute tradeValue
// ---------------------------------------------------------------------------

describe("Rule 1: qty + price → auto-compute tradeValue", () => {
  it("computes tradeValue from qty × price", () => {
    const result = validateTradeValueMatch(10, 182.5, null);
    assert.equal(result.valid, true);
    assert.equal(result.computedTradeValue, 1825);
  });

  it("returns computedTradeValue when tradeValue is undefined", () => {
    const result = validateTradeValueMatch(5, 210.0, undefined);
    assert.equal(result.computedTradeValue, 1050);
  });

  it("fractional qty × price computed correctly", () => {
    const result = validateTradeValueMatch(1.5, 100.0, null);
    assert.equal(result.valid, true);
    assert.equal(result.computedTradeValue, 150);
  });

  it("does not set computedUnitPrice when auto-computing tradeValue", () => {
    const result = validateTradeValueMatch(10, 50, null);
    assert.equal(result.computedUnitPrice, undefined);
  });
});

// ---------------------------------------------------------------------------
// Rule 2: qty + tradeValue → auto-compute unitPrice
// ---------------------------------------------------------------------------

describe("Rule 2: qty + tradeValue → auto-compute unitPrice", () => {
  it("computes unitPrice from tradeValue / qty", () => {
    const result = validateTradeValueMatch(10, null, 1825);
    assert.equal(result.valid, true);
    assert.equal(result.computedUnitPrice, 182.5);
  });

  it("returns computedUnitPrice when unitPrice is undefined", () => {
    const result = validateTradeValueMatch(5, undefined, 1050);
    assert.equal(result.computedUnitPrice, 210);
  });

  it("qty=0 with tradeValue present is valid (zero-cost acquisition)", () => {
    const result = validateTradeValueMatch(0, null, 500);
    assert.equal(result.valid, true);
    assert.equal(result.computedUnitPrice, undefined,
      "Cannot compute unit price when qty=0"
    );
  });
});

// ---------------------------------------------------------------------------
// Rule 3: all three filled → cross-validate
// ---------------------------------------------------------------------------

describe("Rule 3: all three filled → cross-validate", () => {
  it("exact match (10 × 182.5 = 1825) is valid", () => {
    const result = validateTradeValueMatch(10, 182.5, 1825);
    assert.equal(result.valid, true);
    assert.equal(result.error, undefined);
  });

  it("match within 0.01 tolerance is valid", () => {
    // 10 × 182.5 = 1825; tradeValue = 1825.009 → delta = 0.009 ≤ 0.01
    const result = validateTradeValueMatch(10, 182.5, 1825.009);
    assert.equal(result.valid, true);
  });

  it("exact tolerance boundary (delta = 0.01) is valid", () => {
    const result = validateTradeValueMatch(10, 182.5, 1825.01);
    assert.equal(result.valid, true);
  });

  it("exceeds tolerance (delta > 0.01) returns invalid with error", () => {
    // 10 × 182.5 = 1825; tradeValue = 1826 → delta = 1.00 > 0.01
    const result = validateTradeValueMatch(10, 182.5, 1826);
    assert.equal(result.valid, false);
    assert.ok(result.error, "error message must be present");
    assert.ok(
      result.error.includes("Trade value doesn't match"),
      `error must mention mismatch; got: ${result.error}`
    );
  });

  it("large mismatch (100 units off) is invalid", () => {
    const result = validateTradeValueMatch(10, 182.5, 1925);
    assert.equal(result.valid, false);
    assert.ok(result.error);
  });

  it("mismatch 0.011 above tolerance is invalid", () => {
    // 10 × 182.5 = 1825; tradeValue = 1825.011 → delta = 0.011 > 0.01
    const result = validateTradeValueMatch(10, 182.5, 1825.011);
    assert.equal(result.valid, false);
  });

  it("valid match includes expected value in error message (informative)", () => {
    const result = validateTradeValueMatch(10, 182.5, 1900);
    assert.ok(result.error?.includes("1825.00"),
      "Error must show computed expected value (1825.00)"
    );
    assert.ok(result.error?.includes("1900.00"),
      "Error must show provided tradeValue (1900.00)"
    );
  });

  it("qty=0 × price=0, tradeValue=0 is valid (all-zero edge case)", () => {
    const result = validateTradeValueMatch(0, 0, 0);
    assert.equal(result.valid, true);
  });

  it("fractional cross-validation within tolerance", () => {
    // 1.5 × 10.33 = 15.495; tradeValue = 15.50 → delta = 0.005 ≤ 0.01
    const result = validateTradeValueMatch(1.5, 10.33, 15.50);
    assert.equal(result.valid, true);
  });
});

// ---------------------------------------------------------------------------
// Rule 4: only tradeValue (qty zero/empty) → valid
// ---------------------------------------------------------------------------

describe("Rule 4: only tradeValue present → valid", () => {
  it("tradeValue only (no qty, no price) is valid", () => {
    const result = validateTradeValueMatch(null, null, 500);
    assert.equal(result.valid, true);
  });

  it("tradeValue only with undefined fields is valid", () => {
    const result = validateTradeValueMatch(undefined, undefined, 1000);
    assert.equal(result.valid, true);
  });
});

// ---------------------------------------------------------------------------
// Insufficient data → valid (allow form to proceed)
// ---------------------------------------------------------------------------

describe("Insufficient data → valid (no blocking)", () => {
  it("all null is valid (empty form)", () => {
    const result = validateTradeValueMatch(null, null, null);
    assert.equal(result.valid, true);
  });

  it("only quantity provided is valid", () => {
    const result = validateTradeValueMatch(10, null, null);
    assert.equal(result.valid, true);
  });

  it("only price provided is valid", () => {
    const result = validateTradeValueMatch(null, 182.5, null);
    assert.equal(result.valid, true);
  });
});
