/**
 * Pure unit tests for the portfolio zero-share filter predicate.
 *
 * Runs with Node.js built-in test runner — no test framework required:
 *   node --test frontend/tests/filterPortfolioRows.test.mjs
 *
 * The predicate is inlined here (mirrors filterPortfolioRows.ts) because the
 * TypeScript source uses path aliases (@/) and ESM module resolution that
 * require a bundler. Keeping the JS logic in sync with the TS source is the
 * explicit contract; a divergence is a defect.
 *
 * Contract: danny-zero-filter-full-correction-contract.md §A.2
 */

import { test, describe } from "node:test";
import assert from "node:assert/strict";

// ---------------------------------------------------------------------------
// Inline predicate — mirrors filterPortfolioRows.ts exactly.
// Any divergence from the TS source is a defect.
// ---------------------------------------------------------------------------

/** @param {{ portfolio_shares?: string | null }} row */
function shouldShowPortfolioRow(row) {
  if (row.portfolio_shares == null) return true;
  const shares = parseFloat(row.portfolio_shares);
  if (shares < 0) return true;
  return shares !== 0;
}

/** @param {Array<{portfolio_shares?: string | null}>} rows @param {boolean} hideZero */
function filterPortfolioRows(rows, hideZero) {
  if (!hideZero) return rows;
  return rows.filter(shouldShowPortfolioRow);
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** @param {string | null | undefined} portfolio_shares */
function row(portfolio_shares) {
  return { symbol: "AAPL", display_name: "Apple Inc.", portfolio_shares };
}

// ---------------------------------------------------------------------------
// Part A: shouldShowPortfolioRow predicate
// ---------------------------------------------------------------------------

describe("shouldShowPortfolioRow — zero hidden", () => {
  test("exact zero string hides row (A-1)", () => {
    assert.equal(shouldShowPortfolioRow(row("0")), false);
  });

  test("zero with decimals hides row (A-1)", () => {
    assert.equal(shouldShowPortfolioRow(row("0.000000")), false);
  });

  test("zero point zero hides row", () => {
    assert.equal(shouldShowPortfolioRow(row("0.0")), false);
  });
});

describe("shouldShowPortfolioRow — negative visible (A-2)", () => {
  test("negative integer stays visible", () => {
    assert.equal(shouldShowPortfolioRow(row("-50")), true);
  });

  test("negative decimal stays visible", () => {
    assert.equal(shouldShowPortfolioRow(row("-0.5")), true);
  });

  test("negative very small stays visible", () => {
    assert.equal(shouldShowPortfolioRow(row("-0.000001")), true);
  });
});

describe("shouldShowPortfolioRow — null/undefined visible (A-3)", () => {
  test("null portfolio_shares → visible", () => {
    assert.equal(shouldShowPortfolioRow(row(null)), true);
  });

  test("undefined portfolio_shares → visible", () => {
    assert.equal(shouldShowPortfolioRow(row(undefined)), true);
  });

  test("missing key → visible", () => {
    assert.equal(shouldShowPortfolioRow({}), true);
  });
});

describe("shouldShowPortfolioRow — positive non-zero visible", () => {
  test("positive integer visible", () => {
    assert.equal(shouldShowPortfolioRow(row("100")), true);
  });

  test("positive decimal visible", () => {
    assert.equal(shouldShowPortfolioRow(row("25.500000")), true);
  });

  test("positive very small visible", () => {
    assert.equal(shouldShowPortfolioRow(row("0.000001")), true);
  });
});

// ---------------------------------------------------------------------------
// Part B: filterPortfolioRows — toggle semantics
// ---------------------------------------------------------------------------

describe("filterPortfolioRows — toggle ON (hideZero=true)", () => {
  const rows = [
    row("100"),       // visible
    row("0"),         // hidden
    row("-50"),       // visible (negative)
    row(null),        // visible (null)
    row("0.000000"),  // hidden
    row("25"),        // visible
  ];

  test("filter ON hides zero-share rows (A-1)", () => {
    const result = filterPortfolioRows(rows, true);
    const shares = result.map((r) => r.portfolio_shares);
    assert.ok(!shares.includes("0"), "zero share row must be absent");
    assert.ok(!shares.includes("0.000000"), "0.000000 row must be absent");
  });

  test("filter ON keeps positive-share rows", () => {
    const result = filterPortfolioRows(rows, true);
    const shares = result.map((r) => r.portfolio_shares);
    assert.ok(shares.includes("100"), "100-share row must be present");
    assert.ok(shares.includes("25"), "25-share row must be present");
  });

  test("filter ON keeps negative-share rows visible (A-2)", () => {
    const result = filterPortfolioRows(rows, true);
    const shares = result.map((r) => r.portfolio_shares);
    assert.ok(shares.includes("-50"), "negative-share row must remain visible");
  });

  test("filter ON keeps null-shares row visible (A-3)", () => {
    const result = filterPortfolioRows(rows, true);
    const shares = result.map((r) => r.portfolio_shares);
    assert.ok(shares.includes(null), "null-shares row must remain visible");
  });

  test("count reflects visible rows only (A-5)", () => {
    const result = filterPortfolioRows(rows, true);
    assert.equal(result.length, 4, "4 of 6 rows visible (2 zeros hidden)");
  });
});

describe("filterPortfolioRows — toggle OFF (hideZero=false)", () => {
  const rows = [row("0"), row("100"), row(null), row("-50")];

  test("toggle OFF returns all rows (A-7)", () => {
    const result = filterPortfolioRows(rows, false);
    assert.equal(result.length, rows.length, "all rows returned");
  });

  test("toggle OFF returns same array reference (no allocation)", () => {
    const result = filterPortfolioRows(rows, false);
    assert.equal(result, rows, "same reference returned when toggle is OFF");
  });
});

describe("filterPortfolioRows — watchlist isolation (A-4, A-6)", () => {
  // Watchlist rows must never be passed to filterPortfolioRows.
  // This test documents that if they were, they'd pass through because their
  // portfolio_shares is null — showing the predicate is safe even if misused.
  const watchlistRow = { symbol: "MSFT", display_name: "Microsoft", portfolio_shares: null };

  test("watchlist row with null shares survives filter (safe passthrough)", () => {
    const result = filterPortfolioRows([watchlistRow], true);
    assert.equal(result.length, 1, "watchlist-like row not filtered out");
  });
});

describe("filterPortfolioRows — empty state (A-9)", () => {
  test("all zero rows → empty result → empty state condition true", () => {
    const allZero = [row("0"), row("0.000000"), row("0")];
    const result = filterPortfolioRows(allZero, true);
    assert.equal(result.length, 0, "empty array signals empty-state condition");
  });

  test("empty input → empty output", () => {
    const result = filterPortfolioRows([], true);
    assert.equal(result.length, 0);
  });
});

describe("filterPortfolioRows — search composition semantics (A.5 note)", () => {
  // If search and zero-filter are combined (future), both must apply independently.
  // This verifies the filter itself is composable: filter result is a subset of input.
  test("filtered set is a subset of original set", () => {
    const rows = [row("0"), row("100"), row("-10"), row(null)];
    const result = filterPortfolioRows(rows, true);
    for (const r of result) {
      assert.ok(rows.includes(r), "each result row must be in original set");
    }
  });
});
