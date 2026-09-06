/**
 * Date helpers used by the Movements page default date-range.
 *
 * All arithmetic uses local calendar months to avoid UTC off-by-one errors.
 * "Calendar month subtraction" means: same day of month, n months earlier,
 * clamped to the last day of the target month when the source day overflows
 * (e.g. 2024-03-31 minus 1 month → 2024-02-29, not 2024-03-02).
 */

/**
 * Subtract `months` calendar months from `date`.
 * Clamps the day to the last day of the target month when overflow would
 * push into the following month.
 *
 * @example
 * subCalendarMonths(new Date("2026-09-06"), 3)  // 2026-06-06
 * subCalendarMonths(new Date("2024-03-31"), 1)  // 2024-02-29 (leap year)
 * subCalendarMonths(new Date("2023-03-31"), 1)  // 2023-02-28
 */
export function subCalendarMonths(date: Date, months: number): Date {
  const srcYear = date.getFullYear();
  const srcMonth = date.getMonth(); // 0-based
  const srcDay = date.getDate();

  // Raw target month (may be negative)
  const rawMonth = srcMonth - months;
  const targetYear = srcYear + Math.floor(rawMonth / 12);
  const targetMonth = ((rawMonth % 12) + 12) % 12; // always 0–11

  // Last day of target month: day-0 of the next month
  const lastDayOfTarget = new Date(targetYear, targetMonth + 1, 0).getDate();
  const targetDay = Math.min(srcDay, lastDayOfTarget);

  return new Date(targetYear, targetMonth, targetDay);
}

/**
 * Format a Date as a local-time YYYY-MM-DD string, avoiding UTC off-by-one
 * issues that `toISOString().slice(0, 10)` would introduce for dates near
 * midnight in negative-offset timezones.
 */
export function toLocalDateString(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

/**
 * Return the default Movements page date range: today back 3 calendar months.
 * Uses `new Date()` so the result reflects the user's current local time.
 */
export function getDefaultMovementsDateRange(): { from: string; to: string } {
  const today = new Date();
  const from = subCalendarMonths(today, 3);
  return {
    from: toLocalDateString(from),
    to: toLocalDateString(today),
  };
}
