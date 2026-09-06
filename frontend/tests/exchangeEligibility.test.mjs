/**
 * Tests for isUSOptionsEligible (src/lib/exchangeEligibility.ts).
 * Run: node --test tests/exchangeEligibility.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";

// ── Inline helper (mirrors src/lib/exchangeEligibility.ts) ───────────────────

const US_OPTIONS_MICS = new Set(["XNYS", "XNAS"]);

function isUSOptionsEligible(exchangeMic) {
  if (!exchangeMic) return false;
  return US_OPTIONS_MICS.has(exchangeMic.toUpperCase());
}

// ── Allowed MICs ──────────────────────────────────────────────────────────────

test("XNYS (NYSE) is eligible", () => {
  assert.equal(isUSOptionsEligible("XNYS"), true);
});

test("XNAS (Nasdaq) is eligible", () => {
  assert.equal(isUSOptionsEligible("XNAS"), true);
});

test("lowercase xnys is normalised and eligible", () => {
  assert.equal(isUSOptionsEligible("xnys"), true);
});

test("lowercase xnas is normalised and eligible", () => {
  assert.equal(isUSOptionsEligible("xnas"), true);
});

test("mixed-case XnYs is normalised and eligible", () => {
  assert.equal(isUSOptionsEligible("XnYs"), true);
});

// ── Denied MICs ───────────────────────────────────────────────────────────────

test("XMAD (Bolsa Madrid) is not eligible", () => {
  assert.equal(isUSOptionsEligible("XMAD"), false);
});

test("XLON (London Stock Exchange) is not eligible", () => {
  assert.equal(isUSOptionsEligible("XLON"), false);
});

test("XETR (Xetra / Frankfurt) is not eligible", () => {
  assert.equal(isUSOptionsEligible("XETR"), false);
});

test("XAMS (Euronext Amsterdam) is not eligible", () => {
  assert.equal(isUSOptionsEligible("XAMS"), false);
});

test("XPAR (Euronext Paris) is not eligible", () => {
  assert.equal(isUSOptionsEligible("XPAR"), false);
});

// ── Edge cases ────────────────────────────────────────────────────────────────

test("null is not eligible", () => {
  assert.equal(isUSOptionsEligible(null), false);
});

test("undefined is not eligible", () => {
  assert.equal(isUSOptionsEligible(undefined), false);
});

test("empty string is not eligible", () => {
  assert.equal(isUSOptionsEligible(""), false);
});

test("arbitrary string is not eligible", () => {
  assert.equal(isUSOptionsEligible("UNKNOWN"), false);
});
