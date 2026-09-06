/**
 * Regression tests — Reviewer blocker fix (Danny → Reuben escalation, 2026-09-07).
 *
 * Blocker: The backend hides auto-enrolled zero-share ("historical") rows
 * unless the caller sends `include_zero_portfolio=true`, but the frontend
 * Symbols page / BFF proxy never forwarded that query parameter. As a
 * result, the checked-by-default "Hide historical (0 shares)" toggle had
 * nothing to reveal when unchecked — the rows were never fetched at all.
 *
 * Unlike sharedSymbolFilter.test.mjs (pure synthetic predicate tests against
 * an inlined mirror of the client-side filter), these tests exercise the
 * REAL production TypeScript for the two server-side integration points that
 * caused the failure:
 *
 *   1. `src/app/api/symbols/overview/route.ts` — the Next.js BFF proxy route.
 *      Loaded and executed directly (not mirrored) via a custom ESM loader
 *      hook that resolves the `@/` path alias and shims `next/server`, so a
 *      regression in the real file's query-forwarding logic fails this test.
 *
 *   2. `src/app/symbols/page.tsx` — the Symbols page server component. It
 *      contains JSX, which Node's built-in TypeScript type-stripping cannot
 *      parse, so it cannot be `import()`-ed directly in a plain Node test.
 *      Instead we assert against its actual source text that the inclusive
 *      `include_zero_portfolio=true` query parameter is present on the
 *      exact fetch call feeding `SymbolsTable`, catching the same class of
 *      "forgot to forward the flag" regression at the source level.
 *
 * A full simulated round trip (RT-1) additionally proves the end-to-end
 * fix: a fake backend that honors `include_zero_portfolio` (mirroring the
 * real backend contract) is queried through the real route.ts handler, and
 * the resulting payload is fed through the real "hide zero" predicate
 * mirror (kept in sync with SymbolsTable.tsx / sharedSymbolFilter.test.mjs)
 * to prove the historical row becomes visible end-to-end when the toggle
 * is switched off.
 *
 * Contract references:
 *   .squad/decisions/inbox/livingston-unified-watchlist-api-contract.md §2
 *   .squad/decisions/inbox/danny-unified-watchlist-contract.md §5.2
 *
 * Run with: node --test frontend/tests/symbolsOverviewIncludeZeroPortfolio.test.mjs
 */

import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { register } from "node:module";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

// ─── Custom ESM loader: resolves "@/…" and shims "next/server" ─────────────
// Registered once, before importing the real route.ts, so the module under
// test runs unmodified (no inlined mirror) against a real HTTP-shaped mock.
const srcRoot = new URL("../src/", import.meta.url).href;
const hookSource = `
  import { existsSync } from "node:fs";
  import { fileURLToPath } from "node:url";

  export async function resolve(specifier, context, nextResolve) {
    if (specifier === "next/server") {
      return { url: "shim:next-server", shortCircuit: true };
    }
    if (specifier.startsWith("@/")) {
      const rel = specifier.slice(2);
      const base = new URL(${JSON.stringify(srcRoot)});
      for (const ext of ["", ".ts", ".tsx"]) {
        const candidate = new URL(rel + ext, base);
        if (existsSync(fileURLToPath(candidate))) {
          return { url: candidate.href, shortCircuit: true };
        }
      }
    }
    return nextResolve(specifier, context);
  }

  export async function load(url, context, nextLoad) {
    if (url === "shim:next-server") {
      return {
        format: "module",
        shortCircuit: true,
        source: "export const NextResponse = { json: (body, init) => ({ status: (init && init.status) || 200, body }) };",
      };
    }
    return nextLoad(url, context);
  }
`;
register("data:text/javascript," + encodeURIComponent(hookSource), import.meta.url);

const routeModulePromise = import("../src/app/api/symbols/overview/route.ts");

function withMockedFetch(responder, fn) {
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push(String(url));
    return responder(String(url), init);
  };
  return Promise.resolve()
    .then(() => fn(calls))
    .finally(() => {
      globalThis.fetch = original;
    });
}

function jsonResponse(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "OK",
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

describe("BFF route /api/symbols/overview forwards query params (real route.ts)", () => {
  test("RQ-1: no query string on the incoming request forwards no query to the backend", async () => {
    const { GET } = await routeModulePromise;
    await withMockedFetch(
      () => jsonResponse({ symbols: [] }),
      async (calls) => {
        const res = await GET(new Request("http://localhost/api/symbols/overview"));
        assert.equal(calls.length, 1);
        assert.equal(calls[0], "http://localhost:8000/api/symbols/overview");
        assert.equal(res.status, 200);
      },
    );
  });

  test("RQ-2: include_zero_portfolio=true on the incoming request is forwarded verbatim to the backend", async () => {
    const { GET } = await routeModulePromise;
    await withMockedFetch(
      () => jsonResponse({ symbols: [] }),
      async (calls) => {
        await GET(new Request("http://localhost/api/symbols/overview?include_zero_portfolio=true"));
        assert.equal(calls.length, 1);
        assert.equal(
          calls[0],
          "http://localhost:8000/api/symbols/overview?include_zero_portfolio=true",
        );
      },
    );
  });

  test("RQ-3 (regression guard): forwarding must not silently drop the flag (would reproduce Danny's blocker)", async () => {
    const { GET } = await routeModulePromise;
    await withMockedFetch(
      () => jsonResponse({ symbols: [] }),
      async (calls) => {
        await GET(new Request("http://localhost/api/symbols/overview?include_zero_portfolio=true&foo=bar"));
        const forwarded = calls[0];
        assert.ok(
          forwarded.includes("include_zero_portfolio=true"),
          `expected forwarded URL to include include_zero_portfolio=true, got: ${forwarded}`,
        );
      },
    );
  });
});

describe("Symbols page requests the inclusive dataset (source-level guard on page.tsx)", () => {
  // page.tsx renders JSX, which Node's native TS type-stripping cannot parse,
  // so it can't be import()-ed directly in this plain-Node test harness.
  // We instead assert against the real file's source text — this still fails
  // if a future edit reverts to the un-forwarded fetch call that caused
  // Danny's rejection.
  const pageSrc = readFileSync(
    fileURLToPath(new URL("../src/app/symbols/page.tsx", import.meta.url)),
    "utf8",
  );

  test("PG-1: getData() fetches /api/symbols/overview with include_zero_portfolio=true", () => {
    assert.match(
      pageSrc,
      /apiFetch<SymbolsOverview>\(\s*["']\/api\/symbols\/overview\?include_zero_portfolio=true["']\s*\)/,
      "Symbols page must request the inclusive dataset so the historical toggle has zero-share rows to reveal",
    );
  });

  test("PG-2: page still renders exactly one SymbolsTable (single shared toolbar, two internal sections)", () => {
    const matches = pageSrc.match(/<SymbolsTable\b/g) || [];
    assert.equal(matches.length, 1, "expected exactly one <SymbolsTable /> usage");
  });
});

describe("RT-1: full round trip — BFF forward + fake backend visibility + client toggle", () => {
  // Mirror of SymbolsTable.tsx isHiddenZeroRow (kept in sync; see
  // sharedSymbolFilter.test.mjs for the dedicated predicate suite).
  function isHiddenZeroRow(r) {
    if (r.portfolio_shares == null) return false;
    const shares = parseFloat(r.portfolio_shares);
    if (!isFinite(shares) || shares !== 0) return false;
    return r.is_auto_enrolled !== false;
  }

  // Fake backend mirroring the real contract: auto-enrolled zero-share rows
  // are omitted unless include_zero_portfolio=true is present on the query.
  function fakeBackendOverview(queryString) {
    const includeZero = new URLSearchParams(queryString).get("include_zero_portfolio") === "true";
    const historicalRow = {
      symbol: "ZERO",
      portfolio_shares: "0",
      is_auto_enrolled: true,
      row_source: "portfolio",
    };
    const activeRow = {
      symbol: "AAPL",
      portfolio_shares: "10",
      is_auto_enrolled: false,
      row_source: "portfolio",
    };
    const symbols = includeZero ? [activeRow, historicalRow] : [activeRow];
    return { symbols };
  }

  test("without the flag, the historical row is never fetched — no client toggle can reveal it (the original bug)", async () => {
    const { GET } = await routeModulePromise;
    await withMockedFetch(
      (url) => {
        const query = url.split("?")[1] ?? "";
        return jsonResponse(fakeBackendOverview(query));
      },
      async () => {
        const res = await GET(new Request("http://localhost/api/symbols/overview"));
        const rows = res.body.symbols;
        assert.equal(rows.length, 1, "historical row absent from the fetch entirely");
        const visibleWithToggleOff = rows.filter((r) => !isHiddenZeroRow(r));
        assert.equal(visibleWithToggleOff.length, 1);
        assert.ok(
          !rows.some((r) => r.symbol === "ZERO"),
          "ZERO row must be absent — this reproduces Danny's blocker",
        );
      },
    );
  });

  test("with include_zero_portfolio=true, the historical row is fetched and the toggle correctly reveals/hides it", async () => {
    const { GET } = await routeModulePromise;
    await withMockedFetch(
      (url) => {
        const query = url.split("?")[1] ?? "";
        return jsonResponse(fakeBackendOverview(query));
      },
      async () => {
        const res = await GET(
          new Request("http://localhost/api/symbols/overview?include_zero_portfolio=true"),
        );
        const rows = res.body.symbols;
        assert.equal(rows.length, 2, "inclusive fetch must include the historical zero-share row");

        // hideZero = true (default): historical row hidden
        const withToggleOn = rows.filter((r) => !isHiddenZeroRow(r));
        assert.deepEqual(withToggleOn.map((r) => r.symbol), ["AAPL"]);

        // hideZero = false (user unchecks it): historical row revealed
        const withToggleOff = rows; // no filtering applied
        assert.deepEqual(
          withToggleOff.map((r) => r.symbol).sort(),
          ["AAPL", "ZERO"],
        );
      },
    );
  });
});
