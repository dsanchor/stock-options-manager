/**
 * Tests for symbolEncoding.ts — symbol param normalisation helpers.
 *
 * Run with: node --test frontend/tests/symbolEncoding.test.mjs
 */

import { describe, it } from "node:test";
import assert from "node:assert/strict";

// ---------------------------------------------------------------------------
// Inline mirror of src/lib/symbolEncoding.ts
// Keep in sync: any change to the source must be reflected here.
// ---------------------------------------------------------------------------

function decodeSymbolParam(raw) {
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

function symbolHref(securityIdOrTicker) {
  return `/symbols/${securityIdOrTicker}`;
}

// ---------------------------------------------------------------------------
// decodeSymbolParam
// ---------------------------------------------------------------------------

describe("decodeSymbolParam", () => {
  it("leaves a plain legacy ticker unchanged", () => {
    assert.equal(decodeSymbolParam("AAPL"), "AAPL");
  });

  it("leaves an already-decoded MIC:TICKER unchanged", () => {
    assert.equal(decodeSymbolParam("XNYS:AAD"), "XNYS:AAD");
  });

  it("decodes a once-encoded colon (XNYS%3AAAD → XNYS:AAD)", () => {
    assert.equal(decodeSymbolParam("XNYS%3AAAD"), "XNYS:AAD");
  });

  it("decodes lowercase %3a as well", () => {
    assert.equal(decodeSymbolParam("XNYS%3aaad"), "XNYS:aad");
  });

  it("decodes XBRU%3ACOLR to XBRU:COLR (second MIC:TICKER)", () => {
    assert.equal(decodeSymbolParam("XBRU%3ACOLR"), "XBRU:COLR");
  });

  it("leaves XBRU:COLR (already decoded) unchanged", () => {
    assert.equal(decodeSymbolParam("XBRU:COLR"), "XBRU:COLR");
  });

  it("returns the raw value when decodeURIComponent would throw", () => {
    assert.equal(decodeSymbolParam("%ZZ"), "%ZZ");
  });

  it("encodeURIComponent(decodeSymbolParam(once-encoded)) never produces %253A", () => {
    const input = "XNYS%3AAAD"; // once-encoded, as Next.js 16 might deliver
    const result = encodeURIComponent(decodeSymbolParam(input));
    assert.ok(
      !result.includes("%253A"),
      `Must not double-encode: got "${result}"`,
    );
    assert.equal(result, "XNYS%3AAAD");
  });

  it("encodeURIComponent(decodeSymbolParam(decoded)) stays single-encoded", () => {
    const input = "XNYS:AAD"; // decoded form
    const result = encodeURIComponent(decodeSymbolParam(input));
    assert.ok(
      !result.includes("%253A"),
      `Must not double-encode: got "${result}"`,
    );
    assert.equal(result, "XNYS%3AAAD");
  });

  it("XBRU:COLR round-trips without double-encoding", () => {
    for (const input of ["XBRU:COLR", "XBRU%3ACOLR"]) {
      const result = encodeURIComponent(decodeSymbolParam(input));
      assert.ok(!result.includes("%253A"), `Double-encoding for "${input}"`);
      assert.equal(result, "XBRU%3ACOLR");
    }
  });
});

// ---------------------------------------------------------------------------
// symbolHref
// ---------------------------------------------------------------------------

describe("symbolHref", () => {
  it("builds /symbols/AAPL for legacy ticker", () => {
    assert.equal(symbolHref("AAPL"), "/symbols/AAPL");
  });

  it("keeps colon un-encoded in the href (raw pchar)", () => {
    assert.equal(symbolHref("XNYS:AAD"), "/symbols/XNYS:AAD");
  });

  it("keeps colon un-encoded for XBRU:COLR", () => {
    assert.equal(symbolHref("XBRU:COLR"), "/symbols/XBRU:COLR");
  });

  it("href from symbolHref never contains %3A", () => {
    assert.ok(!symbolHref("XNYS:AAD").includes("%3A"));
    assert.ok(!symbolHref("XBRU:COLR").includes("%3A"));
  });
});
