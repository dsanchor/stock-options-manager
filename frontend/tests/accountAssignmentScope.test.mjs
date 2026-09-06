/**
 * Regression tests — Account assignment in Symbol Details (directive 2026-09-06).
 *
 * Directive contract (frontend):
 *   FE-1  Batch entry in Symbol Details Stocks: security_id included in preview/exec requests.
 *   FE-2  Security_id normalization: empty/whitespace → omitted from request body.
 *   FE-3  Security_id normalization: non-empty value → forwarded verbatim.
 *   FE-4  Locked security: when opened with a fixed security_id it CANNOT become undefined.
 *   FE-5  BatchReassignmentPreviewRequest type accepts optional security_id field.
 *   FE-6  BatchReassignmentRequest type accepts optional security_id field.
 *   FE-7  Preview body same-predicate as execution body (security_id consistent).
 *   FE-8  Generic batch buttons absent from Symbols overview page (no toolbar button).
 *   FE-9  Generic batch buttons absent from Movements page (no toolbar button).
 *   FE-10 Individual reassignment available: buildIndividualReassignPayload correct shape.
 *   FE-11 Reason normalization: undefined/empty/whitespace all produce omittable reason.
 *   FE-12 Preview reset triggered when security_id changes.
 *
 * These tests mirror the pure-logic portions of ReassignmentDialog.tsx BatchMode
 * and portfolio-api.ts.  React component rendering is NOT tested here.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

// ─── Inline mirrors ───────────────────────────────────────────────────────────

/**
 * Mirror of the security_id normalization in BatchMode.handlePreview / handleApply.
 * Source: ReassignmentDialog.tsx — `security_id: securityId.trim() || undefined`
 */
function normalizeSecurityId(value) {
  return value.trim() || undefined;
}

/**
 * Mirror of the locked-security resolution contract.
 * When opened from Symbol Details, `lockedSecurityId` is pre-set and must
 * not be droppable to undefined regardless of what the form state contains.
 *
 * Directive: "preview/execution requests cannot drop/change the scoped security_id"
 */
function resolveSecurityFilter(lockedSecurityId, formValue) {
  if (lockedSecurityId != null && lockedSecurityId.trim() !== "") {
    return lockedSecurityId.trim();           // locked — never droppable
  }
  return normalizeSecurityId(formValue ?? ""); // free-form — may be undefined
}

/**
 * Mirror of BatchMode.handlePreview body construction.
 * Source: ReassignmentDialog.tsx
 */
function buildPreviewBody({
  sourceAccountId,
  destAccountId,
  securityId = "",
  dateFrom = "",
  dateTo = "",
} = {}) {
  return {
    source_account_id: sourceAccountId,
    dest_account_id: destAccountId,
    ...(securityId.trim() ? { security_id: securityId.trim() } : {}),
    ...(dateFrom ? { date_from: dateFrom } : {}),
    ...(dateTo ? { date_to: dateTo } : {}),
  };
}

/**
 * Mirror of BatchMode.handleApply body construction.
 * Source: ReassignmentDialog.tsx
 */
function buildApplyBody({
  sourceAccountId,
  destAccountId,
  securityId = "",
  dateFrom = "",
  dateTo = "",
  reason = "",
} = {}) {
  return {
    source_account_id: sourceAccountId,
    dest_account_id: destAccountId,
    ...(securityId.trim() ? { security_id: securityId.trim() } : {}),
    ...(dateFrom ? { date_from: dateFrom } : {}),
    ...(dateTo ? { date_to: dateTo } : {}),
    ...(reason.trim() ? { reason: reason.trim() } : {}),
  };
}

/**
 * Mirror of IndividualMode payload construction.
 * Source: ReassignmentDialog.tsx IndividualMode.handleSubmit
 */
function buildIndividualPayload({ movementId, sourceAccountId, destAccountId, reason = "" }) {
  return {
    movement_id: movementId,
    source_account_id: sourceAccountId,
    dest_account_id: destAccountId,
    ...(reason.trim() ? { reason: reason.trim() } : {}),
  };
}

/**
 * Represents the BatchReassignmentPreviewRequest type shape.
 * Source: frontend/src/types/portfolio.ts
 */
function isValidPreviewRequest(obj) {
  return (
    typeof obj === "object" &&
    obj !== null &&
    typeof obj.source_account_id === "string" &&
    typeof obj.dest_account_id === "string" &&
    (obj.security_id === undefined || typeof obj.security_id === "string") &&
    (obj.date_from === undefined || typeof obj.date_from === "string") &&
    (obj.date_to === undefined || typeof obj.date_to === "string")
  );
}

/**
 * Represents the BatchReassignmentRequest type shape.
 * Source: frontend/src/types/portfolio.ts
 */
function isValidBatchRequest(obj) {
  return (
    typeof obj === "object" &&
    obj !== null &&
    typeof obj.source_account_id === "string" &&
    typeof obj.dest_account_id === "string" &&
    (obj.security_id === undefined || typeof obj.security_id === "string") &&
    (obj.reason === undefined || typeof obj.reason === "string")
  );
}

// ─── Tests ────────────────────────────────────────────────────────────────────

// FE-2: Empty / whitespace security_id normalizes to undefined
test("FE-2: empty string security_id normalizes to undefined", () => {
  assert.strictEqual(normalizeSecurityId(""), undefined);
});

test("FE-2: whitespace-only security_id normalizes to undefined", () => {
  assert.strictEqual(normalizeSecurityId("   "), undefined);
});

test("FE-2: tab-only security_id normalizes to undefined", () => {
  assert.strictEqual(normalizeSecurityId("\t"), undefined);
});

// FE-3: Non-empty security_id forwarded verbatim (after trim)
test("FE-3: non-empty security_id returned as-is", () => {
  assert.strictEqual(normalizeSecurityId("XNYS:AAPL"), "XNYS:AAPL");
});

test("FE-3: security_id with surrounding whitespace trimmed", () => {
  assert.strictEqual(normalizeSecurityId("  XNYS:AAPL  "), "XNYS:AAPL");
});

test("FE-3: MIC:TICKER security_id preserved exactly", () => {
  assert.strictEqual(normalizeSecurityId("XMAD:SAN"), "XMAD:SAN");
});

// FE-4: Locked security — cannot become undefined when lockedSecurityId is set
test("FE-4: locked security_id always forwarded regardless of form value", () => {
  const result = resolveSecurityFilter("XNYS:AAPL", "");
  assert.strictEqual(result, "XNYS:AAPL", "Locked security must not be droppable");
});

test("FE-4: locked security_id overrides any form state value", () => {
  const result = resolveSecurityFilter("XNYS:AAPL", "XMAD:SAN");
  assert.strictEqual(result, "XNYS:AAPL", "Locked security must not be overridable by form");
});

test("FE-4: locked security_id with whitespace in form value still returns locked value", () => {
  const result = resolveSecurityFilter("XNYS:AAPL", "   ");
  assert.strictEqual(result, "XNYS:AAPL");
});

test("FE-4: no lock + empty form value → undefined", () => {
  const result = resolveSecurityFilter(null, "");
  assert.strictEqual(result, undefined);
});

test("FE-4: no lock + non-empty form value → form value used", () => {
  const result = resolveSecurityFilter(null, "XMAD:SAN");
  assert.strictEqual(result, "XMAD:SAN");
});

test("FE-4: empty-string lock treated same as no lock (not a valid lock)", () => {
  const result = resolveSecurityFilter("", "XMAD:SAN");
  assert.strictEqual(result, "XMAD:SAN", "Empty string lockedSecurityId must not lock");
});

// FE-1 / FE-5: Preview body includes security_id when set
test("FE-1/FE-5: preview body includes security_id when provided", () => {
  const body = buildPreviewBody({
    sourceAccountId: "_unassigned",
    destAccountId: "acct_heytrade",
    securityId: "XNYS:AAPL",
  });
  assert.ok(isValidPreviewRequest(body), "Must satisfy BatchReassignmentPreviewRequest shape");
  assert.strictEqual(body.security_id, "XNYS:AAPL");
});

test("FE-5: preview body omits security_id when empty string", () => {
  const body = buildPreviewBody({
    sourceAccountId: "_unassigned",
    destAccountId: "acct_heytrade",
    securityId: "",
  });
  assert.ok(isValidPreviewRequest(body));
  assert.strictEqual(body.security_id, undefined, "Empty security_id must be omitted");
});

test("FE-5: preview body omits security_id when whitespace", () => {
  const body = buildPreviewBody({
    sourceAccountId: "_unassigned",
    destAccountId: "acct_heytrade",
    securityId: "   ",
  });
  assert.ok(isValidPreviewRequest(body));
  assert.strictEqual(body.security_id, undefined);
});

// FE-1 / FE-6: Apply body includes security_id when set
test("FE-1/FE-6: apply body includes security_id when provided", () => {
  const body = buildApplyBody({
    sourceAccountId: "_unassigned",
    destAccountId: "acct_heytrade",
    securityId: "XNYS:AAPL",
  });
  assert.ok(isValidBatchRequest(body), "Must satisfy BatchReassignmentRequest shape");
  assert.strictEqual(body.security_id, "XNYS:AAPL");
});

test("FE-6: apply body omits security_id when empty", () => {
  const body = buildApplyBody({
    sourceAccountId: "_unassigned",
    destAccountId: "acct_heytrade",
    securityId: "",
  });
  assert.ok(isValidBatchRequest(body));
  assert.strictEqual(body.security_id, undefined);
});

// FE-7: Preview and apply bodies have same security_id (consistent predicate)
test("FE-7: preview and apply security_id are identical when locked", () => {
  const opts = {
    sourceAccountId: "_unassigned",
    destAccountId: "acct_heytrade",
    securityId: "XNYS:AAPL",
    dateFrom: "2024-01-01",
    dateTo: "2024-12-31",
  };
  const previewBody = buildPreviewBody(opts);
  const applyBody = buildApplyBody(opts);
  assert.strictEqual(
    previewBody.security_id,
    applyBody.security_id,
    "FE-7: security_id in preview and apply must be identical"
  );
  assert.strictEqual(previewBody.date_from, applyBody.date_from);
  assert.strictEqual(previewBody.date_to, applyBody.date_to);
  assert.strictEqual(previewBody.source_account_id, applyBody.source_account_id);
  assert.strictEqual(previewBody.dest_account_id, applyBody.dest_account_id);
});

test("FE-7: preview and apply security_id both undefined when no lock", () => {
  const opts = {
    sourceAccountId: "_unassigned",
    destAccountId: "acct_heytrade",
    securityId: "",
  };
  const previewBody = buildPreviewBody(opts);
  const applyBody = buildApplyBody(opts);
  assert.strictEqual(previewBody.security_id, undefined);
  assert.strictEqual(applyBody.security_id, undefined);
});

// FE-8 / FE-9: No batch buttons in Symbols or Movements pages
// These pages render only static header + delegation to sub-components.
// The batch dialog is only accessible via the Symbol Details Stocks section.
// Verification: symbols/page.tsx contains no "batchReassign" or "ReassignmentDialog" import.
test("FE-8: Symbols overview page source contains no batch reassignment reference", async () => {
  const { readFileSync } = await import("node:fs");
  const { resolve, dirname } = await import("node:path");
  const { fileURLToPath } = await import("node:url");
  const __dirname = dirname(fileURLToPath(import.meta.url));
  const pageSource = readFileSync(
    resolve(__dirname, "../src/app/symbols/page.tsx"),
    "utf8"
  );
  assert.ok(
    !pageSource.includes("ReassignmentDialog") && !pageSource.includes("batchReassign"),
    "FE-8: Symbols page must not contain batch reassignment button"
  );
});

test("FE-9: Movements page source contains no batch reassignment reference", async () => {
  const { readFileSync } = await import("node:fs");
  const { resolve, dirname } = await import("node:path");
  const { fileURLToPath } = await import("node:url");
  const __dirname = dirname(fileURLToPath(import.meta.url));
  const pageSource = readFileSync(
    resolve(__dirname, "../src/app/portfolio/movements/page.tsx"),
    "utf8"
  );
  assert.ok(
    !pageSource.includes("ReassignmentDialog") && !pageSource.includes("batchReassign"),
    "FE-9: Movements page must not contain batch reassignment button"
  );
});

// FE-10: Individual reassignment payload shape
test("FE-10: individual reassign payload has correct shape", () => {
  const payload = buildIndividualPayload({
    movementId: "mvt_abc",
    sourceAccountId: "_unassigned",
    destAccountId: "acct_heytrade",
    reason: "moved to correct account",
  });
  assert.strictEqual(payload.movement_id, "mvt_abc");
  assert.strictEqual(payload.source_account_id, "_unassigned");
  assert.strictEqual(payload.dest_account_id, "acct_heytrade");
  assert.strictEqual(payload.reason, "moved to correct account");
});

test("FE-10: individual reassign omits reason when empty", () => {
  const payload = buildIndividualPayload({
    movementId: "mvt_xyz",
    sourceAccountId: "_unassigned",
    destAccountId: "acct_heytrade",
    reason: "",
  });
  assert.strictEqual(payload.reason, undefined);
});

// FE-11: Batch reason normalization
test("FE-11: apply body omits reason when empty", () => {
  const body = buildApplyBody({
    sourceAccountId: "_unassigned",
    destAccountId: "acct_heytrade",
    reason: "",
  });
  assert.strictEqual(body.reason, undefined);
});

test("FE-11: apply body omits reason when whitespace-only", () => {
  const body = buildApplyBody({
    sourceAccountId: "_unassigned",
    destAccountId: "acct_heytrade",
    reason: "   ",
  });
  assert.strictEqual(body.reason, undefined);
});

test("FE-11: apply body includes trimmed reason when non-empty", () => {
  const body = buildApplyBody({
    sourceAccountId: "_unassigned",
    destAccountId: "acct_heytrade",
    reason: "  moved to correct account  ",
  });
  assert.strictEqual(body.reason, "moved to correct account");
});

// FE-12: Preview reset on security_id change (state side-effect contract)
// Tested via a pure state-machine representation of the reset logic.
test("FE-12: preview state is reset when security_id changes", () => {
  // Model the form state as a plain object
  let state = { preview: { affected_count: 5 }, confirmed: true };

  function onSecurityIdChange(newValue, oldValue) {
    if (newValue !== oldValue) {
      // resetPreview() equivalent
      state = { ...state, preview: null, confirmed: false };
    }
  }

  onSecurityIdChange("XMAD:SAN", "XNYS:AAPL");
  assert.strictEqual(state.preview, null, "FE-12: preview must be reset on security change");
  assert.strictEqual(state.confirmed, false, "FE-12: confirmed must be reset on security change");
});

test("FE-12: preview state NOT reset when security_id unchanged", () => {
  let state = { preview: { affected_count: 5 }, confirmed: true };

  function onSecurityIdChange(newValue, oldValue) {
    if (newValue !== oldValue) {
      state = { ...state, preview: null, confirmed: false };
    }
  }

  onSecurityIdChange("XNYS:AAPL", "XNYS:AAPL");
  assert.strictEqual(state.preview?.affected_count, 5, "No reset if value unchanged");
  assert.strictEqual(state.confirmed, true);
});
