/**
 * Eligibility rules for US-exchange-only features (options agents, tracking
 * controls, Buy Tracker, Alerts, Analyze sub-pages, Options section).
 *
 * Allowed MICs: XNYS (NYSE) and XNAS (Nasdaq).
 * All other MICs, unknown, or missing exchanges are ineligible.
 *
 * Backend enforcement is handled separately. This helper drives the
 * frontend gate and is the single source of truth for MIC membership.
 */

const US_OPTIONS_MICS = new Set(["XNYS", "XNAS"]);

/**
 * Returns true only when the exchange MIC is XNYS or XNAS.
 * Normalises to uppercase before comparing so casing in API responses
 * does not cause silent denial.
 */
export function isUSOptionsEligible(exchangeMic: string | null | undefined): boolean {
  if (!exchangeMic) return false;
  return US_OPTIONS_MICS.has(exchangeMic.toUpperCase());
}
