/**
 * Tests for symbolDetailVisibility.ts — Amendment I §I.3.2 section visibility.
 *
 * Run with: node --test frontend/tests/symbolDetailVisibility.test.mjs
 *
 * Inline predicates mirror symbolDetailVisibility.ts exactly.
 * Any divergence is a defect.
 */

import { describe, it } from "node:test";
import assert from "node:assert/strict";

// ---------------------------------------------------------------------------
// Inline predicates (mirror symbolDetailVisibility.ts — update both together)
// ---------------------------------------------------------------------------

function shouldShowOptionsSection({ symbolState, positionCount, activityCount }) {
  const hasAgentContent =
    symbolState === "watchlist_only" ||
    symbolState === "watchlist_and_portfolio" ||
    symbolState == null;
  return hasAgentContent || positionCount > 0 || activityCount > 0;
}

function shouldShowStocksSection({ portfolio }) {
  return portfolio != null;
}

// ---------------------------------------------------------------------------
// shouldShowOptionsSection
// ---------------------------------------------------------------------------

describe("shouldShowOptionsSection", () => {
  // hasAgentContent states
  it("watchlist_only → show Options (agent state)", () => {
    assert.equal(
      shouldShowOptionsSection({ symbolState: "watchlist_only", positionCount: 0, activityCount: 0 }),
      true,
    );
  });

  it("watchlist_and_portfolio → show Options (agent state)", () => {
    assert.equal(
      shouldShowOptionsSection({ symbolState: "watchlist_and_portfolio", positionCount: 0, activityCount: 0 }),
      true,
    );
  });

  it("null symbolState (legacy/unknown) → show Options", () => {
    assert.equal(
      shouldShowOptionsSection({ symbolState: null, positionCount: 0, activityCount: 0 }),
      true,
    );
  });

  it("undefined symbolState → show Options", () => {
    assert.equal(
      shouldShowOptionsSection({ symbolState: undefined, positionCount: 0, activityCount: 0 }),
      true,
    );
  });

  // portfolio_only / portfolio_historical — show only if data present
  it("portfolio_only + no positions + no activities → hide Options", () => {
    assert.equal(
      shouldShowOptionsSection({ symbolState: "portfolio_only", positionCount: 0, activityCount: 0 }),
      false,
      "Options must be hidden for portfolio_only with no positions or activities"
    );
  });

  it("portfolio_historical + no positions + no activities → hide Options", () => {
    assert.equal(
      shouldShowOptionsSection({ symbolState: "portfolio_historical", positionCount: 0, activityCount: 0 }),
      false,
      "Options must be hidden for portfolio_historical with no content"
    );
  });

  it("portfolio_only + 1 position → show Options", () => {
    assert.equal(
      shouldShowOptionsSection({ symbolState: "portfolio_only", positionCount: 1, activityCount: 0 }),
      true,
      "Options must show when positions exist even for portfolio_only"
    );
  });

  it("portfolio_only + 0 positions + 1 activity → show Options", () => {
    assert.equal(
      shouldShowOptionsSection({ symbolState: "portfolio_only", positionCount: 0, activityCount: 1 }),
      true,
      "Options must show when activities exist"
    );
  });

  it("portfolio_historical + multiple positions → show Options", () => {
    assert.equal(
      shouldShowOptionsSection({ symbolState: "portfolio_historical", positionCount: 3, activityCount: 0 }),
      true,
    );
  });

  it("watchlist_only + large positionCount → still shows Options", () => {
    assert.equal(
      shouldShowOptionsSection({ symbolState: "watchlist_only", positionCount: 10, activityCount: 5 }),
      true,
    );
  });

  // Acceptance criteria I-4: Options section hidden for portfolio_only/historical without content
  it("I-4: portfolio_only with zero counts → hidden (acceptance criterion)", () => {
    assert.equal(
      shouldShowOptionsSection({ symbolState: "portfolio_only", positionCount: 0, activityCount: 0 }),
      false,
      "I-4: Options section must be hidden when no positions, no activities, and portfolio_only"
    );
  });
});

// ---------------------------------------------------------------------------
// shouldShowStocksSection
// ---------------------------------------------------------------------------

describe("shouldShowStocksSection", () => {
  it("portfolio object present → show Stocks", () => {
    const portfolio = { current_shares: "100" };
    assert.equal(shouldShowStocksSection({ portfolio }), true);
  });

  it("portfolio with zero shares → still shows Stocks (historical position)", () => {
    const portfolio = { current_shares: "0" };
    assert.equal(shouldShowStocksSection({ portfolio }), true,
      "portfolio_historical has portfolio != null → Stocks must show"
    );
  });

  it("portfolio null → hide Stocks (watchlist_only)", () => {
    assert.equal(shouldShowStocksSection({ portfolio: null }), false,
      "watchlist_only symbols have portfolio=null; Stocks must be hidden"
    );
  });

  it("portfolio undefined → hide Stocks", () => {
    assert.equal(shouldShowStocksSection({ portfolio: undefined }), false);
  });

  it("empty portfolio object (no current_shares field) → show Stocks", () => {
    // portfolio present even without all fields → still hasPortfolio = true
    assert.equal(shouldShowStocksSection({ portfolio: {} }), true);
  });

  it("portfolio with recent_movements empty array → show Stocks", () => {
    const portfolio = { current_shares: "50", recent_movements: [] };
    assert.equal(shouldShowStocksSection({ portfolio }), true);
  });

  // Acceptance criterion I-5
  it("I-5: watchlist_only (portfolio=null) → Stocks hidden (acceptance criterion)", () => {
    assert.equal(shouldShowStocksSection({ portfolio: null }), false,
      "I-5: Stocks section must be hidden when portfolio is null (watchlist_only)"
    );
  });

  it("I-2: portfolio present → Stocks shown (acceptance criterion)", () => {
    assert.equal(
      shouldShowStocksSection({ portfolio: { current_shares: "10" } }),
      true,
      "I-2: Stocks section must be shown when portfolio is not null"
    );
  });
});

// ---------------------------------------------------------------------------
// Combined: both sections for all symbol states
// ---------------------------------------------------------------------------

describe("Combined section visibility by symbolState", () => {
  it("watchlist_only: Options=true, Stocks=false", () => {
    const opts = shouldShowOptionsSection({ symbolState: "watchlist_only", positionCount: 0, activityCount: 0 });
    const stocks = shouldShowStocksSection({ portfolio: null });
    assert.equal(opts, true);
    assert.equal(stocks, false);
  });

  it("portfolio_only with movements: Options=false, Stocks=true", () => {
    const opts = shouldShowOptionsSection({ symbolState: "portfolio_only", positionCount: 0, activityCount: 0 });
    const stocks = shouldShowStocksSection({ portfolio: { current_shares: "50" } });
    assert.equal(opts, false);
    assert.equal(stocks, true);
  });

  it("watchlist_and_portfolio: Options=true, Stocks=true", () => {
    const opts = shouldShowOptionsSection({ symbolState: "watchlist_and_portfolio", positionCount: 0, activityCount: 0 });
    const stocks = shouldShowStocksSection({ portfolio: { current_shares: "100" } });
    assert.equal(opts, true);
    assert.equal(stocks, true);
  });

  it("portfolio_historical with no positions: Options=false, Stocks=true", () => {
    const opts = shouldShowOptionsSection({ symbolState: "portfolio_historical", positionCount: 0, activityCount: 0 });
    const stocks = shouldShowStocksSection({ portfolio: { current_shares: "0" } });
    assert.equal(opts, false);
    assert.equal(stocks, true);
  });
});
