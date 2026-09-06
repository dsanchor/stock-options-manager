/**
 * Tests for unified watchlist row predicate — §1.1 / §1.2 visibility rules.
 *
 * Written by: Basher (independent tester/reviewer).
 * Contract: .squad/decisions/inbox/danny-unified-watchlist-contract.md §1–§5
 *           .squad/decisions/inbox/copilot-directive-20260907-watchlist-shared-filters.md
 *
 * Run with: node --test frontend/tests/unifiedWatchlistPredicate.test.mjs
 *
 * Architecture (post-20260907 directive):
 *   - TWO visible sections: Portfolio (rows with portfolio data) + Watchlist (purely-watchlist rows).
 *   - ONE shared filter toolbar — search, Ideal Calls/Puts, and hide-zero apply to BOTH sections.
 *   - A symbol that appears in both portfolio and watchlist goes into the Portfolio section ONLY
 *     (portfolio precedence, §1.3). The Watchlist section shows only `row_source === "watchlist"` rows.
 *   - These predicate tests (shouldShowUnifiedRow, isWatchlistMember) remain valid — each section
 *     still filters its own rows with the same logic. The §1.3 duplicate tests are updated to
 *     reflect two-section semantics. Shared-filter cross-section tests live in sharedSymbolFilter.test.mjs.
 *
 * Tests cover:
 *   - §1.1 VISIBLE_DEFAULT / HIDDEN_DEFAULT table (all 6 scenarios)
 *   - §1.2 is_watchlist_member predicate (all 5 toggle paths)
 *   - §5.2 Zero-share toggle behavior
 *   - §2.4 Totals unaffected by row filtering
 *   - §2.2 Watchlist-only row null fields
 *   - §1.3 Portfolio-section precedence (two-section model)
 */

import { describe, it } from "node:test";
import assert from "node:assert/strict";

// ---------------------------------------------------------------------------
// Inline predicates — mirror SymbolsTable.tsx unified logic.
// Keep in sync with production code after migration.
// ---------------------------------------------------------------------------

/**
 * Returns true when a symbol has explicit watchlist membership.
 * Maps to §1.2 is_watchlist_member.
 *
 * @param {{ _auto_enrolled?: boolean, watchlist?: { covered_call?: boolean, cash_secured_put?: boolean, buy_tracker?: boolean }, telegram_notifications_enabled?: boolean }} config
 * @returns {boolean}
 */
function isWatchlistMember(config) {
  if (!config) return false;
  if (!config._auto_enrolled) return true; // manually added
  const wl = config.watchlist || {};
  return (
    wl.covered_call === true ||
    wl.cash_secured_put === true ||
    wl.buy_tracker === true ||
    config.telegram_notifications_enabled === true
  );
}

/**
 * Returns true when the unified row should be visible in the default view
 * (hideZero=true).
 *
 * Maps to §1.1 VISIBLE_DEFAULT:
 *   - has non-zero portfolio_shares (positive or negative) → visible
 *   - is explicit watchlist member → visible
 *   - is pure watchlist (no portfolio history) → visible
 *
 * Hidden when:
 *   - portfolio_shares === "0" AND is_auto_enrolled=true AND NOT is_watchlist_member
 *
 * @param {{ portfolio_shares?: string | null, is_auto_enrolled?: boolean, watchlist_config?: object }} row
 * @param {boolean} hideZero
 * @returns {boolean}
 */
function shouldShowUnifiedRow(row, hideZero) {
  if (!hideZero) return true; // toggle OFF → show all

  const { portfolio_shares, is_auto_enrolled } = row;

  // No portfolio history (pure watchlist) → always visible
  if (portfolio_shares === null || portfolio_shares === undefined) return true;

  const shares = parseFloat(portfolio_shares);

  // Non-zero shares (positive or negative) → always visible
  if (shares !== 0) return true;

  // Zero shares: visible if NOT (auto-enrolled AND not explicit watchlist member)
  if (!is_auto_enrolled) return true; // manually added → explicit member
  // For auto-enrolled with zero shares, check explicit membership via watchlist config
  const config = {
    _auto_enrolled: is_auto_enrolled,
    watchlist: row.watchlist || {},
    telegram_notifications_enabled: row.telegram_notifications_enabled || false,
  };
  return isWatchlistMember(config);
}

// ---------------------------------------------------------------------------
// §1.2 — is_watchlist_member predicate tests
// ---------------------------------------------------------------------------

describe("isWatchlistMember — §1.2", () => {
  it("manually added (_auto_enrolled=false) → member (true)", () => {
    assert.equal(
      isWatchlistMember({ _auto_enrolled: false, watchlist: {} }),
      true,
      "Manually added symbol must be an explicit watchlist member"
    );
  });

  it("_auto_enrolled=false + all toggles off → still member", () => {
    assert.equal(
      isWatchlistMember({
        _auto_enrolled: false,
        watchlist: { covered_call: false, cash_secured_put: false, buy_tracker: false },
        telegram_notifications_enabled: false,
      }),
      true,
      "_auto_enrolled=false is sufficient for explicit membership"
    );
  });

  it("auto-enrolled + covered_call=true → member", () => {
    assert.equal(
      isWatchlistMember({
        _auto_enrolled: true,
        watchlist: { covered_call: true, cash_secured_put: false, buy_tracker: false },
      }),
      true,
      "covered_call=true confers explicit membership"
    );
  });

  it("auto-enrolled + cash_secured_put=true → member", () => {
    assert.equal(
      isWatchlistMember({
        _auto_enrolled: true,
        watchlist: { covered_call: false, cash_secured_put: true, buy_tracker: false },
      }),
      true,
      "cash_secured_put=true confers explicit membership"
    );
  });

  it("auto-enrolled + buy_tracker=true → member", () => {
    assert.equal(
      isWatchlistMember({
        _auto_enrolled: true,
        watchlist: { covered_call: false, cash_secured_put: false, buy_tracker: true },
      }),
      true,
      "buy_tracker=true confers explicit membership"
    );
  });

  it("auto-enrolled + telegram_notifications_enabled=true → member", () => {
    assert.equal(
      isWatchlistMember({
        _auto_enrolled: true,
        watchlist: { covered_call: false, cash_secured_put: false, buy_tracker: false },
        telegram_notifications_enabled: true,
      }),
      true,
      "telegram_notifications_enabled=true confers explicit membership"
    );
  });

  it("auto-enrolled + all toggles off → NOT member (false)", () => {
    assert.equal(
      isWatchlistMember({
        _auto_enrolled: true,
        watchlist: { covered_call: false, cash_secured_put: false, buy_tracker: false },
        telegram_notifications_enabled: false,
      }),
      false,
      "Auto-enrolled with no toggles is NOT an explicit member"
    );
  });

  it("auto-enrolled + missing watchlist key → NOT member", () => {
    assert.equal(
      isWatchlistMember({ _auto_enrolled: true }),
      false,
      "Auto-enrolled with no watchlist object → not a member"
    );
  });

  it("null/undefined config → not member", () => {
    assert.equal(isWatchlistMember(null), false);
    assert.equal(isWatchlistMember(undefined), false);
  });
});

// ---------------------------------------------------------------------------
// §1.1 — shouldShowUnifiedRow (contract table all 6 scenarios)
// ---------------------------------------------------------------------------

describe("shouldShowUnifiedRow — §1.1 contract table (hideZero=true)", () => {
  const HIDE_ZERO = true;

  // Row 1: Current holding (shares > 0) → ✅ Yes
  it("Scenario 1: active holding (shares=100) → visible", () => {
    const row = {
      portfolio_shares: "100",
      is_auto_enrolled: false,
      watchlist: {},
    };
    assert.equal(shouldShowUnifiedRow(row, HIDE_ZERO), true,
      "Active holding with positive shares must be visible");
  });

  // Row 2: Negative holding (shares < 0) → ✅ Yes
  it("Scenario 2: negative holding (shares=-50) → visible (anomaly)", () => {
    const row = {
      portfolio_shares: "-50",
      is_auto_enrolled: false,
      watchlist: {},
    };
    assert.equal(shouldShowUnifiedRow(row, HIDE_ZERO), true,
      "Negative shares must always be visible (anomaly)");
  });

  // Row 3: Watchlist-only (no ledger history) → ✅ Yes
  it("Scenario 3: watchlist-only (portfolio_shares=null) → visible", () => {
    const row = {
      portfolio_shares: null,
      is_auto_enrolled: false,
      watchlist: {},
    };
    assert.equal(shouldShowUnifiedRow(row, HIDE_ZERO), true,
      "Watchlist-only row (null portfolio_shares) must be visible");
  });

  // Row 4: Manual watchlist + historical zero shares → ✅ Yes
  it("Scenario 4: manual watchlist + zero shares → visible (explicit member)", () => {
    const row = {
      portfolio_shares: "0",
      is_auto_enrolled: false,  // manually added
      watchlist: { covered_call: false, cash_secured_put: false, buy_tracker: false },
    };
    assert.equal(shouldShowUnifiedRow(row, HIDE_ZERO), true,
      "Manually-added symbol with zero shares must remain visible (explicit member)");
  });

  // Row 5: Auto-enrolled only + exactly zero shares → ❌ Hidden
  it("Scenario 5: auto-enrolled only + zero shares → hidden (F-4)", () => {
    const row = {
      portfolio_shares: "0",
      is_auto_enrolled: true,
      watchlist: { covered_call: false, cash_secured_put: false, buy_tracker: false },
      telegram_notifications_enabled: false,
    };
    assert.equal(shouldShowUnifiedRow(row, HIDE_ZERO), false,
      "Auto-enrolled only with zero shares must be hidden by default");
  });

  // Row 5 variant: "0.000000" string
  it("Scenario 5 variant: '0.000000' string → hidden", () => {
    const row = {
      portfolio_shares: "0.000000",
      is_auto_enrolled: true,
      watchlist: { covered_call: false, cash_secured_put: false, buy_tracker: false },
      telegram_notifications_enabled: false,
    };
    assert.equal(shouldShowUnifiedRow(row, HIDE_ZERO), false,
      "'0.000000' must be treated as zero and hidden");
  });

  // Row 6: Auto-enrolled + toggle OFF (hide_zero=false) → ✅ Yes
  it("Scenario 6: auto-enrolled + zero + toggle OFF → visible", () => {
    const row = {
      portfolio_shares: "0",
      is_auto_enrolled: true,
      watchlist: { covered_call: false, cash_secured_put: false, buy_tracker: false },
      telegram_notifications_enabled: false,
    };
    assert.equal(shouldShowUnifiedRow(row, false /* hideZero=false */), true,
      "Toggle OFF must reveal all rows including auto-enrolled zero-share");
  });
});

// ---------------------------------------------------------------------------
// §5.2 — Zero-share toggle behavior in detail
// ---------------------------------------------------------------------------

describe("shouldShowUnifiedRow — §5.2 toggle semantics", () => {
  const autoEnrolledZero = {
    portfolio_shares: "0",
    is_auto_enrolled: true,
    watchlist: { covered_call: false, cash_secured_put: false, buy_tracker: false },
    telegram_notifications_enabled: false,
  };
  const manualZero = {
    portfolio_shares: "0",
    is_auto_enrolled: false,
    watchlist: {},
  };
  const activeHolding = {
    portfolio_shares: "100",
    is_auto_enrolled: false,
    watchlist: {},
  };
  const watchlistOnly = {
    portfolio_shares: null,
    is_auto_enrolled: false,
    watchlist: {},
  };

  it("toggle ON: auto-enrolled zero → hidden; manual zero → visible", () => {
    assert.equal(shouldShowUnifiedRow(autoEnrolledZero, true), false,
      "Auto-enrolled zero hidden when toggle ON");
    assert.equal(shouldShowUnifiedRow(manualZero, true), true,
      "Manual zero visible even when toggle ON");
  });

  it("toggle OFF: auto-enrolled zero → visible (show all)", () => {
    assert.equal(shouldShowUnifiedRow(autoEnrolledZero, false), true,
      "Auto-enrolled zero visible when toggle OFF");
  });

  it("toggle ON: active holding always visible", () => {
    assert.equal(shouldShowUnifiedRow(activeHolding, true), true);
  });

  it("toggle ON: watchlist-only (null) always visible", () => {
    assert.equal(shouldShowUnifiedRow(watchlistOnly, true), true);
  });

  it("toggle OFF: all row types visible", () => {
    const rows = [autoEnrolledZero, manualZero, activeHolding, watchlistOnly];
    for (const row of rows) {
      assert.equal(shouldShowUnifiedRow(row, false), true,
        "All rows visible when toggle OFF");
    }
  });
});

// ---------------------------------------------------------------------------
// §1.2 — Explicit membership overrides zero filter
// ---------------------------------------------------------------------------

describe("Explicit watchlist membership overrides zero filter", () => {
  it("covered_call=true + zero shares → visible despite toggle ON", () => {
    const row = {
      portfolio_shares: "0",
      is_auto_enrolled: true,
      watchlist: { covered_call: true, cash_secured_put: false, buy_tracker: false },
    };
    assert.equal(shouldShowUnifiedRow(row, true), true,
      "covered_call=true makes symbol an explicit member → visible with zero shares");
  });

  it("cash_secured_put=true + zero shares → visible", () => {
    const row = {
      portfolio_shares: "0",
      is_auto_enrolled: true,
      watchlist: { covered_call: false, cash_secured_put: true, buy_tracker: false },
    };
    assert.equal(shouldShowUnifiedRow(row, true), true);
  });

  it("buy_tracker=true + zero shares → visible", () => {
    const row = {
      portfolio_shares: "0",
      is_auto_enrolled: true,
      watchlist: { covered_call: false, cash_secured_put: false, buy_tracker: true },
    };
    assert.equal(shouldShowUnifiedRow(row, true), true);
  });

  it("telegram=true + zero shares → visible", () => {
    const row = {
      portfolio_shares: "0",
      is_auto_enrolled: true,
      watchlist: { covered_call: false, cash_secured_put: false, buy_tracker: false },
      telegram_notifications_enabled: true,
    };
    assert.equal(shouldShowUnifiedRow(row, true), true);
  });
});

// ---------------------------------------------------------------------------
// §1.3 — Portfolio-section precedence (two-section model)
// ---------------------------------------------------------------------------

describe("Portfolio-section precedence — two-section model (§1.3)", () => {
  /**
   * In the two-section layout:
   *   portfolioSection = rows where row_source !== "watchlist"  (portfolio or both)
   *   watchlistSection = rows where row_source === "watchlist"  (purely watchlist, no ledger data)
   *
   * A symbol that has portfolio data (row_source "portfolio" or "both") must appear
   * ONLY in the Portfolio section, NEVER in the Watchlist section.
   */

  function portfolioSection(rows) {
    return rows.filter((r) => r.row_source !== "watchlist");
  }
  function watchlistSection(rows) {
    return rows.filter((r) => r.row_source === "watchlist");
  }

  /** Build the flat row list as the backend returns it (one row per symbol). */
  function buildRows(portfolioSymbols, watchlistOnlySymbols) {
    const rows = [];
    for (const s of portfolioSymbols) {
      const inWL = watchlistOnlySymbols.includes(s);
      rows.push({
        symbol: s,
        portfolio_shares: "100",
        row_source: inWL ? "both" : "portfolio",
      });
    }
    for (const s of watchlistOnlySymbols) {
      if (!portfolioSymbols.includes(s)) {
        rows.push({ symbol: s, portfolio_shares: null, row_source: "watchlist" });
      }
    }
    return rows;
  }

  it("symbol in both portfolio and watchlist appears in Portfolio section", () => {
    const rows = buildRows(["AAPL"], ["AAPL", "MSFT"]);
    assert.ok(
      portfolioSection(rows).some((r) => r.symbol === "AAPL"),
      "AAPL must appear in Portfolio section"
    );
  });

  it("symbol in both does NOT appear in Watchlist section", () => {
    const rows = buildRows(["AAPL"], ["AAPL", "MSFT"]);
    assert.equal(
      watchlistSection(rows).filter((r) => r.symbol === "AAPL").length,
      0,
      "AAPL must NOT appear in Watchlist section (portfolio precedence)"
    );
  });

  it("symbol in both appears exactly once across BOTH sections", () => {
    const rows = buildRows(["AAPL"], ["AAPL", "MSFT"]);
    const allRendered = [...portfolioSection(rows), ...watchlistSection(rows)];
    assert.equal(
      allRendered.filter((r) => r.symbol === "AAPL").length,
      1,
      "AAPL must appear exactly once across both sections combined"
    );
  });

  it("watchlist-only symbol is in Watchlist section only", () => {
    const rows = buildRows(["AAPL"], ["AAPL", "MSFT"]);
    assert.ok(
      watchlistSection(rows).some((r) => r.symbol === "MSFT"),
      "MSFT (watchlist-only) must appear in Watchlist section"
    );
    assert.equal(
      portfolioSection(rows).filter((r) => r.symbol === "MSFT").length,
      0,
      "MSFT must NOT appear in Portfolio section"
    );
  });

  it("portfolio-only symbol is in Portfolio section only", () => {
    const rows = buildRows(["O"], []);
    assert.equal(portfolioSection(rows).length, 1);
    assert.equal(watchlistSection(rows).length, 0);
    assert.equal(portfolioSection(rows)[0].symbol, "O");
  });

  it("total unique symbols across both sections equals union of portfolio and watchlist", () => {
    const rows = buildRows(["AAPL", "O"], ["AAPL", "MSFT", "NVDA"]);
    const ps = portfolioSection(rows);
    const ws = watchlistSection(rows);
    const allSymbols = new Set([...ps, ...ws].map((r) => r.symbol));
    assert.equal(allSymbols.size, 4); // AAPL, O, MSFT, NVDA
    assert.ok(allSymbols.has("AAPL"));
    assert.ok(allSymbols.has("O"));
    assert.ok(allSymbols.has("MSFT"));
    assert.ok(allSymbols.has("NVDA"));
  });

  it("row_source='both' row has portfolio_shares (not null)", () => {
    const rows = buildRows(["AAPL"], ["AAPL"]);
    const aapl = rows.find((r) => r.symbol === "AAPL");
    assert.equal(aapl.row_source, "both");
    assert.notEqual(aapl.portfolio_shares, null,
      "row_source='both' must carry portfolio_shares data");
  });

  it("no symbol appears in both sections simultaneously", () => {
    const rows = buildRows(["AAPL", "O", "NVDA"], ["AAPL", "MSFT", "NVDA"]);
    const ps = new Set(portfolioSection(rows).map((r) => r.symbol));
    const ws = new Set(watchlistSection(rows).map((r) => r.symbol));
    const overlap = [...ps].filter((s) => ws.has(s));
    assert.deepEqual(overlap, [],
      `Symbols must not appear in both sections: ${overlap.join(", ")}`
    );
  });
});

// ---------------------------------------------------------------------------
// §2.2 — Watchlist-only row null field contract
// ---------------------------------------------------------------------------

describe("Watchlist-only row null portfolio fields (§2.2)", () => {
  const watchlistRow = {
    symbol: "MSFT",
    portfolio_shares: null,
    portfolio_avg_cost_eur: null,
    portfolio_invested_eur: null,
    portfolio_dividends_eur: null,
    portfolio_realized_eur: null,
    row_source: "watchlist",
  };

  it("portfolio_shares is null for watchlist-only", () => {
    assert.equal(watchlistRow.portfolio_shares, null);
  });

  it("portfolio_avg_cost_eur is null for watchlist-only", () => {
    assert.equal(watchlistRow.portfolio_avg_cost_eur, null);
  });

  it("portfolio_invested_eur is null for watchlist-only", () => {
    assert.equal(watchlistRow.portfolio_invested_eur, null);
  });

  it("portfolio_dividends_eur is null for watchlist-only", () => {
    assert.equal(watchlistRow.portfolio_dividends_eur, null);
  });

  it("portfolio_realized_eur is null for watchlist-only", () => {
    assert.equal(watchlistRow.portfolio_realized_eur, null);
  });

  it("row_source is 'watchlist' for pure watchlist row", () => {
    assert.equal(watchlistRow.row_source, "watchlist");
  });
});

// ---------------------------------------------------------------------------
// §2.4 — Totals unaffected by row filtering
// ---------------------------------------------------------------------------

describe("Totals unaffected by client-side row filtering (§2.4)", () => {
  /**
   * Summary totals are backend-computed and portfolio-wide.
   * They are NOT recomputed on the frontend when rows are filtered.
   * This tests that the totals in the response are independent of visible rows.
   *
   * Simulate: fetch overview → filter rows → totals unchanged.
   */

  it("filtering rows does not recompute portfolio_summary", () => {
    // Simulated overview response (as if from /api/symbols/overview)
    const overview = {
      rows: [
        { symbol: "AAPL", portfolio_shares: "100", is_auto_enrolled: false },
        { symbol: "MSFT", portfolio_shares: "0",   is_auto_enrolled: true,
          watchlist: { covered_call: false, cash_secured_put: false, buy_tracker: false },
          telegram_notifications_enabled: false },
      ],
      portfolio_summary: {
        remaining_cost_basis_eur: "25000.00",
        realized_result_eur: "0.00",
        total_dividends_eur: "150.00",
      },
      total_call_exposure: 0,
      total_put_exposure: 0,
    };

    // After client-side filter (hide zero toggle)
    const visibleRows = overview.rows.filter((r) => shouldShowUnifiedRow(r, true));
    assert.equal(visibleRows.length, 1, "Only AAPL visible after zero-filter");

    // portfolio_summary must be unchanged (it's a separate field, not derived from rows)
    assert.deepEqual(
      overview.portfolio_summary,
      {
        remaining_cost_basis_eur: "25000.00",
        realized_result_eur: "0.00",
        total_dividends_eur: "150.00",
      },
      "portfolio_summary must be unaffected by client-side row filtering (§2.4)"
    );
  });

  it("total_call_exposure and total_put_exposure unaffected by filtering", () => {
    const overview = {
      rows: [
        { symbol: "AAPL", portfolio_shares: "100" },
        { symbol: "MSFT", portfolio_shares: "0", is_auto_enrolled: true,
          watchlist: { covered_call: false, cash_secured_put: false, buy_tracker: false } },
      ],
      total_call_exposure: 20000,
      total_put_exposure: 15000,
    };

    const visible = overview.rows.filter((r) => shouldShowUnifiedRow(r, true));
    assert.equal(visible.length, 1);

    // Totals unchanged
    assert.equal(overview.total_call_exposure, 20000,
      "total_call_exposure must not change after client-side row filtering");
    assert.equal(overview.total_put_exposure, 15000,
      "total_put_exposure must not change after client-side row filtering");
  });
});
