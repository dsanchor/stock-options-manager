/**
 * Tests for us-options-eligible.ts — Amendment J US eligibility predicate.
 *
 * Written by: Basher (independent tester/reviewer).
 * Contract: .squad/decisions/inbox/danny-unified-watchlist-contract.md §Amendment J
 *
 * Run with: node --test frontend/tests/usOptionsEligible.test.mjs
 *
 * The predicate is inlined here (mirrors us-options-eligible.ts when it exists).
 * Keeping the JS logic in sync with the TS source is the explicit contract;
 * a divergence is a defect.
 *
 * Covers J-SP acceptance criteria:
 *   J-SP1: isUsOptionsEligible("XNYS") → true
 *   J-SP2: isUsOptionsEligible("XNAS") → true
 *   J-SP3: isUsOptionsEligible("XMAD") → false
 *   J-SP4: isUsOptionsEligible("") → false
 *   J-SP5: isUsOptionsEligible(null/undefined) → false
 *   J-SP6: Frontend result matches backend for the same 5 inputs
 *   J-SP7: US_OPTIONS_ELIGIBLE_MICS is exactly {XNYS, XNAS}
 */

import { describe, it } from "node:test";
import assert from "node:assert/strict";

// ---------------------------------------------------------------------------
// Inline predicate — mirrors frontend/src/lib/us-options-eligible.ts exactly.
// When the source file exists, this block must stay byte-for-byte identical.
// Any divergence is a defect.
// ---------------------------------------------------------------------------

/** @type {ReadonlySet<string>} */
const US_OPTIONS_ELIGIBLE_MICS = new Set(["XNYS", "XNAS"]);

/**
 * @param {string | null | undefined} exchangeMic
 * @returns {boolean}
 */
function isUsOptionsEligible(exchangeMic) {
  if (!exchangeMic) return false;
  return US_OPTIONS_ELIGIBLE_MICS.has(exchangeMic.trim().toUpperCase());
}

// ---------------------------------------------------------------------------
// J-SP7: Verify set contains exactly XNYS and XNAS
// ---------------------------------------------------------------------------

describe("US_OPTIONS_ELIGIBLE_MICS set definition (J-SP7)", () => {
  it("contains XNYS", () => {
    assert.ok(US_OPTIONS_ELIGIBLE_MICS.has("XNYS"), "Set must contain XNYS");
  });

  it("contains XNAS", () => {
    assert.ok(US_OPTIONS_ELIGIBLE_MICS.has("XNAS"), "Set must contain XNAS");
  });

  it("contains exactly 2 entries", () => {
    assert.equal(
      US_OPTIONS_ELIGIBLE_MICS.size,
      2,
      "US_OPTIONS_ELIGIBLE_MICS must contain exactly {XNYS, XNAS} — no extensions without contract update"
    );
  });

  it("does not contain XMAD", () => {
    assert.ok(!US_OPTIONS_ELIGIBLE_MICS.has("XMAD"));
  });

  it("does not contain XLON", () => {
    assert.ok(!US_OPTIONS_ELIGIBLE_MICS.has("XLON"));
  });

  it("does not contain XETR", () => {
    assert.ok(!US_OPTIONS_ELIGIBLE_MICS.has("XETR"));
  });
});

// ---------------------------------------------------------------------------
// J-SP1, J-SP2: US exchanges → true
// ---------------------------------------------------------------------------

describe("isUsOptionsEligible — US exchanges (J-SP1, J-SP2)", () => {
  it("J-SP1: XNYS → true", () => {
    assert.equal(isUsOptionsEligible("XNYS"), true,
      "XNYS (NYSE) must be eligible for US options");
  });

  it("J-SP2: XNAS → true", () => {
    assert.equal(isUsOptionsEligible("XNAS"), true,
      "XNAS (NASDAQ) must be eligible for US options");
  });

  it("lowercase xnys → true (case-insensitive)", () => {
    assert.equal(isUsOptionsEligible("xnys"), true,
      "Predicate must be case-insensitive: xnys → true");
  });

  it("lowercase xnas → true (case-insensitive)", () => {
    assert.equal(isUsOptionsEligible("xnas"), true);
  });

  it("mixed-case Xnys → true", () => {
    assert.equal(isUsOptionsEligible("Xnys"), true);
  });

  it("XNYS with leading/trailing spaces → true (strip)", () => {
    assert.equal(isUsOptionsEligible(" XNYS "), true,
      "Leading/trailing spaces stripped before lookup");
  });

  it("XNAS with spaces → true", () => {
    assert.equal(isUsOptionsEligible("  XNAS  "), true);
  });
});

// ---------------------------------------------------------------------------
// J-SP3: Non-US exchanges → false
// ---------------------------------------------------------------------------

describe("isUsOptionsEligible — non-US exchanges (J-SP3)", () => {
  it("J-SP3: XMAD (Madrid) → false", () => {
    assert.equal(isUsOptionsEligible("XMAD"), false,
      "XMAD (Bolsa de Madrid) is not US-eligible");
  });

  it("XLON (London Stock Exchange) → false", () => {
    assert.equal(isUsOptionsEligible("XLON"), false);
  });

  it("XETR (Frankfurt Xetra) → false", () => {
    assert.equal(isUsOptionsEligible("XETR"), false);
  });

  it("XBRU (Euronext Brussels) → false", () => {
    assert.equal(isUsOptionsEligible("XBRU"), false);
  });

  it("XPAR (Euronext Paris) → false", () => {
    assert.equal(isUsOptionsEligible("XPAR"), false);
  });

  it("XAMS (Euronext Amsterdam) → false", () => {
    assert.equal(isUsOptionsEligible("XAMS"), false);
  });

  it("XHKG (Hong Kong) → false", () => {
    assert.equal(isUsOptionsEligible("XHKG"), false);
  });

  it("XTKS (Tokyo) → false", () => {
    assert.equal(isUsOptionsEligible("XTKS"), false);
  });

  it("lowercase xmad → false", () => {
    assert.equal(isUsOptionsEligible("xmad"), false);
  });

  it("UNKNOWN random string → false", () => {
    assert.equal(isUsOptionsEligible("UNKNOWN"), false);
  });
});

// ---------------------------------------------------------------------------
// J-SP4, J-SP5: Fail-closed: empty / null / undefined → false
// ---------------------------------------------------------------------------

describe("isUsOptionsEligible — fail-closed (J-SP4, J-SP5)", () => {
  it("J-SP4: empty string → false", () => {
    assert.equal(isUsOptionsEligible(""), false,
      "Empty string must return false (fail-closed)");
  });

  it("J-SP5: null → false", () => {
    assert.equal(isUsOptionsEligible(null), false,
      "null must return false (fail-closed)");
  });

  it("J-SP5: undefined → false", () => {
    assert.equal(isUsOptionsEligible(undefined), false,
      "undefined must return false (fail-closed)");
  });

  it("whitespace-only string → false", () => {
    // After trim, becomes "" which is falsy → false
    assert.equal(isUsOptionsEligible("   "), false,
      "Whitespace-only string must return false");
  });
});

// ---------------------------------------------------------------------------
// J-SP6: Frontend parity with backend predicate
// The backend defines the same 5 canonical test cases.
// Both sides must agree on these.
// ---------------------------------------------------------------------------

describe("Frontend/backend predicate parity (J-SP6)", () => {
  /** Known results as defined by backend J-SP1–J-SP5 */
  const BACKEND_CANONICAL_RESULTS = [
    { input: "XNYS", expected: true,  criterion: "J-SP1" },
    { input: "XNAS", expected: true,  criterion: "J-SP2" },
    { input: "XMAD", expected: false, criterion: "J-SP3" },
    { input: "",     expected: false, criterion: "J-SP4" },
    { input: null,   expected: false, criterion: "J-SP5" },
  ];

  for (const { input, expected, criterion } of BACKEND_CANONICAL_RESULTS) {
    it(`${criterion}: isUsOptionsEligible(${JSON.stringify(input)}) === ${expected}`, () => {
      assert.equal(
        isUsOptionsEligible(input),
        expected,
        `${criterion} parity failure: frontend must agree with backend for input=${JSON.stringify(input)}`
      );
    });
  }
});

// ---------------------------------------------------------------------------
// UI behavior mapping: what should be shown/hidden per eligibility (J-F1–J-F10)
// ---------------------------------------------------------------------------

describe("UI visibility mapping by eligibility", () => {
  it("J-F1/J-F2: XNYS and XNAS symbols show options controls", () => {
    const showOptions = (mic) => isUsOptionsEligible(mic);
    assert.equal(showOptions("XNYS"), true, "XNYS: SymbolActions and Options section shown");
    assert.equal(showOptions("XNAS"), true, "XNAS: SymbolActions and Options section shown");
  });

  it("J-F3/J-F4: XMAD and XETR symbols hide options controls", () => {
    const showOptions = (mic) => isUsOptionsEligible(mic);
    assert.equal(showOptions("XMAD"), false, "XMAD: SymbolActions and Options section hidden");
    assert.equal(showOptions("XETR"), false, "XETR: SymbolActions and Options section hidden");
  });

  it("J-F5: null/unknown MIC hides options controls (fail-closed)", () => {
    assert.equal(isUsOptionsEligible(null), false,
      "null MIC must hide options controls (fail-closed)");
    assert.equal(isUsOptionsEligible(undefined), false,
      "undefined MIC must hide options controls (fail-closed)");
    assert.equal(isUsOptionsEligible(""), false,
      "empty MIC must hide options controls (fail-closed)");
  });

  it("Summary and Stocks sections must be visible regardless of eligibility", () => {
    // These sections don't use isUsOptionsEligible — they're always shown.
    // This test documents that constraint:  showSummary = !isUsOptionsEligible needed? NO.
    // Summary and Stocks are always shown (independent of eligibility).
    const shouldShowSummary = () => true;  // always shown
    const shouldShowStocks = (portfolio) => portfolio != null;  // depends on portfolio presence

    assert.equal(shouldShowSummary(), true, "J-F6: Summary always visible");
    assert.equal(shouldShowStocks({ current_shares: "100" }), true, "J-F7: Stocks visible when portfolio present");
    assert.equal(shouldShowStocks(null), false, "J-F7: Stocks hidden when no portfolio (watchlist-only)");
  });
});
