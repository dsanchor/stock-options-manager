import type { SecurityMaster } from "@/types/portfolio";

/**
 * Pure filter helper: case-insensitive search across security_id, ticker,
 * company_name, and all SecurityAlias values.
 *
 * Returns the same array reference when query is blank (no allocation).
 */
export function filterSecurities(
  securities: SecurityMaster[],
  query: string,
): SecurityMaster[] {
  const q = query.trim().toLowerCase();
  if (!q) return securities;

  return securities.filter((s) => {
    if (s.security_id.toLowerCase().includes(q)) return true;
    if (s.ticker.toLowerCase().includes(q)) return true;
    if (s.company_name.toLowerCase().includes(q)) return true;
    if (s.aliases?.some((a) => a.value.toLowerCase().includes(q))) return true;
    return false;
  });
}
