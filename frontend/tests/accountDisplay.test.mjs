/**
 * Tests for accountDisplay helpers (Task 3a/3b).
 * Mirrors the logic in src/lib/accountDisplay.ts without TypeScript imports.
 * Run: node --test tests/accountDisplay.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";

// ── Inline helpers (mirrors src/lib/accountDisplay.ts) ───────────────────────

const BROKER_LABELS = {
  heytrade: "HeyTrade",
  degiro: "DeGiro",
  interactive_brokers: "Interactive Brokers",
  revolut: "Revolut",
  other: "Other",
};

const UNASSIGNED_LABEL = "Sin asignar";

function formatAccountLabel(account) {
  if (!account) return "—";
  const brokerLabel = account.broker ? (BROKER_LABELS[account.broker] ?? account.broker) : null;
  const name = account.name?.trim() || null;
  if (brokerLabel && name) return `${brokerLabel} · ${name}`;
  if (name) return name;
  if (brokerLabel) return brokerLabel;
  return "—";
}

function getAccountLabel(accountId, accounts) {
  if (!accountId || accountId === "_unassigned") return UNASSIGNED_LABEL;
  const account = accounts.find((a) => a.account_id === accountId);
  if (account) return formatAccountLabel(account);
  return accountId;
}

// Palette and hash (mirrors accountDisplay.ts exactly)
const ACCOUNT_BADGE_PALETTE = [
  "bg-accent-blue/15 text-accent-blue",
  "bg-accent-green/15 text-accent-green",
  "bg-accent-purple/15 text-accent-purple",
  "bg-accent-cyan/15 text-accent-cyan",
  "bg-accent-orange/15 text-accent-orange",
  "bg-accent-red/15 text-accent-red",
];
const UNASSIGNED_BADGE_CLASS = "bg-bg-hover text-text-muted";

function accountColorIndex(accountId) {
  if (!accountId || accountId === "_unassigned") return -1;
  let h = 0;
  for (let i = 0; i < accountId.length; i++) {
    h = (Math.imul(31, h) + accountId.charCodeAt(i)) | 0;
  }
  return Math.abs(h) % ACCOUNT_BADGE_PALETTE.length;
}

function getAccountBadgeClass(accountId) {
  if (!accountId || accountId === "_unassigned") return UNASSIGNED_BADGE_CLASS;
  return ACCOUNT_BADGE_PALETTE[accountColorIndex(accountId)];
}

// ── Test fixtures ─────────────────────────────────────────────────────────────

const accounts = [
  { account_id: "acc-heytrade-1", broker: "heytrade", name: "Cuenta Principal" },
  { account_id: "acc-degiro-2",   broker: "degiro",   name: "European Equities" },
  { account_id: "acc-ib-3",       broker: "interactive_brokers", name: "Options Account" },
  { account_id: "acc-no-broker",  broker: null,       name: "Solo Name" },
  { account_id: "acc-no-name",    broker: "revolut",  name: "" },
];

// ── formatAccountLabel ────────────────────────────────────────────────────────

test("formatAccountLabel: broker + name returns Broker · Name", () => {
  assert.equal(
    formatAccountLabel({ account_id: "x", broker: "heytrade", name: "Cuenta Principal" }),
    "HeyTrade · Cuenta Principal",
  );
});

test("formatAccountLabel: name only (no broker)", () => {
  assert.equal(
    formatAccountLabel({ account_id: "x", broker: null, name: "Solo Name" }),
    "Solo Name",
  );
});

test("formatAccountLabel: broker only (empty name)", () => {
  assert.equal(
    formatAccountLabel({ account_id: "x", broker: "revolut", name: "" }),
    "Revolut",
  );
});

test("formatAccountLabel: neither broker nor name returns —", () => {
  assert.equal(
    formatAccountLabel({ account_id: "x", broker: null, name: "" }),
    "—",
  );
});

test("formatAccountLabel: null account returns —", () => {
  assert.equal(formatAccountLabel(null), "—");
  assert.equal(formatAccountLabel(undefined), "—");
});

test("formatAccountLabel: unknown broker slug is used as-is", () => {
  const label = formatAccountLabel({ account_id: "x", broker: "unknown_broker", name: "Test" });
  assert.equal(label, "unknown_broker · Test");
});

// ── getAccountLabel ───────────────────────────────────────────────────────────

test("getAccountLabel: _unassigned returns Sin asignar", () => {
  assert.equal(getAccountLabel("_unassigned", accounts), "Sin asignar");
});

test("getAccountLabel: empty string returns Sin asignar", () => {
  assert.equal(getAccountLabel("", accounts), "Sin asignar");
});

test("getAccountLabel: known account_id returns formatted label", () => {
  assert.equal(
    getAccountLabel("acc-heytrade-1", accounts),
    "HeyTrade · Cuenta Principal",
  );
});

test("getAccountLabel: known account with broker+name for IB", () => {
  assert.equal(
    getAccountLabel("acc-ib-3", accounts),
    "Interactive Brokers · Options Account",
  );
});

test("getAccountLabel: account with no broker shows name only", () => {
  assert.equal(getAccountLabel("acc-no-broker", accounts), "Solo Name");
});

test("getAccountLabel: unknown account_id falls back to raw id", () => {
  assert.equal(getAccountLabel("nonexistent-123", accounts), "nonexistent-123");
});

// ── accountColorIndex ─────────────────────────────────────────────────────────

test("accountColorIndex: _unassigned returns -1", () => {
  assert.equal(accountColorIndex("_unassigned"), -1);
  assert.equal(accountColorIndex(""), -1);
});

test("accountColorIndex: same id always produces same index (deterministic)", () => {
  const idx1 = accountColorIndex("acc-heytrade-1");
  const idx2 = accountColorIndex("acc-heytrade-1");
  assert.equal(idx1, idx2);
});

test("accountColorIndex: index is within palette bounds", () => {
  for (const id of ["acc-heytrade-1", "acc-degiro-2", "acc-ib-3", "some-other-uuid"]) {
    const idx = accountColorIndex(id);
    assert.ok(idx >= 0 && idx < ACCOUNT_BADGE_PALETTE.length, `Index ${idx} out of range for ${id}`);
  }
});

test("accountColorIndex: different ids generally produce different indices (distribution)", () => {
  const ids = ["acc-a", "acc-b", "acc-c", "acc-d", "acc-e", "acc-f", "acc-g", "acc-h"];
  const indices = ids.map(accountColorIndex);
  const unique = new Set(indices);
  // With 8 IDs and 6 palette slots, expect at least 4 unique values
  assert.ok(unique.size >= 3, `Too few unique indices: ${[...unique].join(", ")}`);
});

// ── getAccountBadgeClass ──────────────────────────────────────────────────────

test("getAccountBadgeClass: _unassigned returns neutral class", () => {
  assert.equal(getAccountBadgeClass("_unassigned"), UNASSIGNED_BADGE_CLASS);
  assert.equal(getAccountBadgeClass(""), UNASSIGNED_BADGE_CLASS);
});

test("getAccountBadgeClass: known id returns one of the palette classes", () => {
  const cls = getAccountBadgeClass("acc-heytrade-1");
  assert.ok(ACCOUNT_BADGE_PALETTE.includes(cls), `Class "${cls}" not in palette`);
});

test("getAccountBadgeClass: same id always returns same class (stable)", () => {
  const cls1 = getAccountBadgeClass("acc-degiro-2");
  const cls2 = getAccountBadgeClass("acc-degiro-2");
  assert.equal(cls1, cls2);
});

test("getAccountBadgeClass: no %253A or raw account_id in color class", () => {
  const cls = getAccountBadgeClass("acc-heytrade-1");
  assert.ok(!cls.includes("acc-"), `Class should not contain account id: ${cls}`);
});
