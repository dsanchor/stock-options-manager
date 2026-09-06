/**
 * MIC → yfinance suffix mapping.
 * Mirror of backend/src/portfolio/provider_symbols.py — must stay in sync.
 * Unknown MICs are not in this map; callers receive `null` (no fabrication).
 */
export const MIC_TO_YFINANCE_SUFFIX: Record<string, string> = {
  XMAD: ".MC",
  XAMS: ".AS",
  XLON: ".L",
  XPAR: ".PA",
  XETR: ".DE",
  XSWX: ".SW",
  XBRU: ".BR",
  XLIS: ".LS",
  XNYS: "",
  XNAS: "",
};

/**
 * Returns the suggested yfinance symbol for a given ticker + MIC, or `null`
 * when the MIC is not in the approved table (no fabrication for unknown exchanges).
 *
 * Examples:
 *   suggestYfinanceSymbol("ENG", "XMAD") → "ENG.MC"
 *   suggestYfinanceSymbol("AAPL", "XNYS") → "AAPL"
 *   suggestYfinanceSymbol("FOO", "XZZZ") → null
 */
export function suggestYfinanceSymbol(
  ticker: string,
  mic: string,
): string | null {
  const suffix = MIC_TO_YFINANCE_SUFFIX[mic.toUpperCase()];
  if (suffix === undefined) return null;
  return `${ticker.toUpperCase()}${suffix}`;
}
