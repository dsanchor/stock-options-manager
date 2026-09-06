/**
 * salesTypeLabels.ts — User-facing labels for internal sales_type enum values.
 *
 * Amendment G §G.3.2: the internal API/DB enum stays ACCIONES/DERECHOS.
 * The UI renders "Stocks" / "Rights" via this mapping everywhere sales_type
 * is displayed (MovementDetailDialog, PortfolioMovementsTable, StockTransactionsTable,
 * ImportPreview).
 *
 * Internal enum values must NEVER be shown directly to the user.
 */

import { SALES_TYPE_LABELS } from "@/types/portfolio";

export { SALES_TYPE_LABELS };

/**
 * Returns the user-facing label for a sales_type enum value, or null if the
 * value is absent / not a recognised enum key.
 *
 * UI usage:
 *   const label = getSalesTypeLabel(movement.sales_type);
 *   if (label) <span>{label}</span>
 */
export function getSalesTypeLabel(
  salesType: string | null | undefined,
): string | null {
  if (!salesType) return null;
  return (
    (SALES_TYPE_LABELS as Record<string, string>)[salesType] ?? null
  );
}
