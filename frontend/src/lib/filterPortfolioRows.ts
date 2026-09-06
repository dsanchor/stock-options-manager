import type { SymbolRow } from "@/types/symbols";

/**
 * Pure predicate: should a portfolio row be visible when "hide zero-share" is ON?
 *
 * Rules (contract §A.2):
 *   - `portfolio_shares` parses to exactly 0 → HIDDEN
 *   - `portfolio_shares` is null / undefined → VISIBLE (conservative; no data yet)
 *   - `portfolio_shares` parses to a negative number → VISIBLE (anomaly; must see)
 *   - Any non-zero positive value → VISIBLE
 *
 * Watchlist rows must NEVER be passed to this helper; pass them through unchanged.
 */
export function shouldShowPortfolioRow(
  row: Pick<SymbolRow, "portfolio_shares">,
): boolean {
  if (row.portfolio_shares == null) return true;
  const shares = parseFloat(row.portfolio_shares);
  if (shares < 0) return true;
  return shares !== 0;
}

/**
 * Filter `portfolioRows` by the zero-share predicate.
 *
 * When `hideZero` is false the original array reference is returned unchanged
 * (no allocation).  Watchlist rows are a separate concern and must not be passed
 * here.
 */
export function filterPortfolioRows(
  rows: SymbolRow[],
  hideZero: boolean,
): SymbolRow[] {
  if (!hideZero) return rows;
  return rows.filter(shouldShowPortfolioRow);
}
