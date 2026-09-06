/**
 * filterMovementsByType.ts — Pure helper for the Stocks tab.
 *
 * The Stocks tab requirement (Symbol Details, §6 contract) shows BUY, SELL,
 * and DIVIDEND movements for the current security. TRANSFER_IN / TRANSFER_OUT
 * movements appear in the backend payload today (get_movements() called without
 * txn_type filter). This helper provides client-side filtering until the backend
 * detail endpoint is tightened to pass txn_type constraints.
 *
 * Pattern: mirrors filterSecurities.ts and filterPortfolioRows.ts conventions.
 */

export type StocksTabTxnType = "BUY" | "SELL" | "DIVIDEND";

export const STOCKS_TAB_TYPES: readonly StocksTabTxnType[] = [
  "BUY",
  "SELL",
  "DIVIDEND",
];

/**
 * Returns true if the given txn_type should appear in the Stocks tab.
 * TRANSFER_IN, TRANSFER_OUT, and any unknown types are excluded.
 */
export function isStocksTabMovement(txn_type: string): boolean {
  return (STOCKS_TAB_TYPES as readonly string[]).includes(txn_type);
}

/**
 * Filters a movements array to only those types shown in the Stocks tab.
 * Preserves original order (backend returns newest-first by trade_date).
 */
export function filterMovementsForStocksTab<
  T extends { txn_type: string },
>(movements: T[]): T[] {
  return movements.filter((m) => isStocksTabMovement(m.txn_type));
}
