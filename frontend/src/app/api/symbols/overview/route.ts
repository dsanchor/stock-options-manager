import { NextResponse } from "next/server";
import { apiFetch } from "@/lib/api";
import type { SymbolsOverview } from "@/types/symbols";

/**
 * BFF proxy: browser → this Next route → internal Python API.
 * Mirrors the backend's GET /api/symbols/overview endpoint (lightweight rows
 * used by the TopNav symbol search autocomplete and the Symbols list page).
 *
 * Forwards the incoming query string as-is (e.g. `include_zero_portfolio=true`)
 * so callers that need the inclusive auto-enrolled/zero-share dataset can
 * request it without this proxy silently dropping the parameter. See
 * livingston-unified-watchlist-api-contract.md.
 */
export async function GET(request: Request) {
  try {
    const { search } = new URL(request.url);
    const data = await apiFetch<SymbolsOverview>(`/api/symbols/overview${search}`);
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Upstream API error" },
      { status: 502 },
    );
  }
}
