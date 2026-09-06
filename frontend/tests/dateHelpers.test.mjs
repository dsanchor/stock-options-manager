/**
 * Tests for dateHelpers.ts — calendar-month arithmetic and date formatting.
 *
 * Run with: node --test frontend/tests/dateHelpers.test.mjs
 */

import { describe, it } from "node:test";
import assert from "node:assert/strict";

// ---------------------------------------------------------------------------
// Inline mirror of src/lib/dateHelpers.ts
// Keep in sync with the source.
// ---------------------------------------------------------------------------

function subCalendarMonths(date, months) {
  const srcYear = date.getFullYear();
  const srcMonth = date.getMonth();
  const srcDay = date.getDate();

  const rawMonth = srcMonth - months;
  const targetYear = srcYear + Math.floor(rawMonth / 12);
  const targetMonth = ((rawMonth % 12) + 12) % 12;

  const lastDayOfTarget = new Date(targetYear, targetMonth + 1, 0).getDate();
  const targetDay = Math.min(srcDay, lastDayOfTarget);

  return new Date(targetYear, targetMonth, targetDay);
}

function toLocalDateString(date) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

// ---------------------------------------------------------------------------
// subCalendarMonths
// ---------------------------------------------------------------------------

describe("subCalendarMonths", () => {
  it("2026-09-06 minus 3 months = 2026-06-06", () => {
    const result = subCalendarMonths(new Date(2026, 8, 6), 3);
    assert.equal(toLocalDateString(result), "2026-06-06");
  });

  it("2026-08-31 minus 3 months = 2026-05-31 (May has 31 days)", () => {
    const result = subCalendarMonths(new Date(2026, 7, 31), 3);
    assert.equal(toLocalDateString(result), "2026-05-31");
  });

  it("2024-03-31 minus 1 month = 2024-02-29 (leap year)", () => {
    const result = subCalendarMonths(new Date(2024, 2, 31), 1);
    assert.equal(toLocalDateString(result), "2024-02-29");
  });

  it("2023-03-31 minus 1 month = 2023-02-28 (non-leap year)", () => {
    const result = subCalendarMonths(new Date(2023, 2, 31), 1);
    assert.equal(toLocalDateString(result), "2023-02-28");
  });

  it("2026-01-31 minus 3 months = 2025-10-31 (crosses year boundary)", () => {
    const result = subCalendarMonths(new Date(2026, 0, 31), 3);
    assert.equal(toLocalDateString(result), "2025-10-31");
  });

  it("2026-03-30 minus 3 months = 2025-12-30 (crosses year boundary, day fits)", () => {
    const result = subCalendarMonths(new Date(2026, 2, 30), 3);
    assert.equal(toLocalDateString(result), "2025-12-30");
  });

  it("2026-11-30 minus 3 months = 2026-08-30 (Sep has 30 — no overflow)", () => {
    const result = subCalendarMonths(new Date(2026, 10, 30), 3);
    assert.equal(toLocalDateString(result), "2026-08-30");
  });

  it("2026-04-30 minus 1 month = 2026-03-30 (March has 31 — no clamp)", () => {
    const result = subCalendarMonths(new Date(2026, 3, 30), 1);
    assert.equal(toLocalDateString(result), "2026-03-30");
  });

  it("2026-05-31 minus 1 month = 2026-04-30 (April has 30, clamp 31→30)", () => {
    const result = subCalendarMonths(new Date(2026, 4, 31), 1);
    assert.equal(toLocalDateString(result), "2026-04-30");
  });

  it("mid-month ordinary case: 2026-07-15 minus 3 months = 2026-04-15", () => {
    const result = subCalendarMonths(new Date(2026, 6, 15), 3);
    assert.equal(toLocalDateString(result), "2026-04-15");
  });
});

// ---------------------------------------------------------------------------
// toLocalDateString
// ---------------------------------------------------------------------------

describe("toLocalDateString", () => {
  it("formats Jan 1 correctly with zero-padded month and day", () => {
    assert.equal(toLocalDateString(new Date(2026, 0, 1)), "2026-01-01");
  });

  it("formats Dec 31 correctly", () => {
    assert.equal(toLocalDateString(new Date(2026, 11, 31)), "2026-12-31");
  });

  it("pads single-digit month", () => {
    assert.equal(toLocalDateString(new Date(2026, 5, 9)), "2026-06-09");
  });

  it("pads single-digit day", () => {
    assert.equal(toLocalDateString(new Date(2026, 11, 5)), "2026-12-05");
  });

  it("result format matches YYYY-MM-DD", () => {
    const s = toLocalDateString(new Date(2026, 8, 6));
    assert.match(s, /^\d{4}-\d{2}-\d{2}$/);
  });
});
