/**
 * symbolDetailVisibility.ts — Pure helpers for Amendment I section visibility.
 *
 * Symbol Detail page (§I.3.2): two stacked sections — "Options" and "Stocks".
 * Both are collapsible, default expanded. Visibility driven by symbol_state
 * and data presence.
 *
 * Pattern: mirrors filterSecurities.ts / filterPortfolioRows.ts conventions.
 */

import type { SymbolState } from "@/types/symbol-detail";

export interface OptionsSectionInput {
  symbolState: SymbolState | null | undefined;
  /** Positions array length */
  positionCount: number;
  /** Activities array length */
  activityCount: number;
}

export interface StocksSectionInput {
  /** portfolio field from SymbolDetail response — null means no ledger */
  portfolio: { current_shares?: string | null } | null | undefined;
}

/**
 * Determines whether the Options section should be shown.
 *
 * Section shown when: hasAgentContent OR positions exist OR activities exist.
 * hasAgentContent = symbolState is watchlist_only | watchlist_and_portfolio | null/unknown.
 *
 * Amendment I §I.3.2 visibility rule:
 *   Options section hidden only when no positions, no activities, and
 *   symbolState is portfolio_only or portfolio_historical (no agent content).
 */
export function shouldShowOptionsSection({
  symbolState,
  positionCount,
  activityCount,
}: OptionsSectionInput): boolean {
  const hasAgentContent =
    symbolState === "watchlist_only" ||
    symbolState === "watchlist_and_portfolio" ||
    symbolState == null; // legacy / unknown state

  return hasAgentContent || positionCount > 0 || activityCount > 0;
}

/**
 * Determines whether the Stocks section should be shown.
 *
 * Section shown when: portfolio is not null (symbol has ledger entries).
 * watchlist_only symbols have portfolio=null → Stocks section hidden.
 *
 * Amendment I §I.3.2 visibility rule: Stocks shown when hasPortfolio.
 */
export function shouldShowStocksSection({
  portfolio,
}: StocksSectionInput): boolean {
  return portfolio != null;
}
