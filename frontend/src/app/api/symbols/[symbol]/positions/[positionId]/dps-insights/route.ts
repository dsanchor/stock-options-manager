import { NextResponse } from "next/server";
import { API_BASE_URL } from "@/lib/api";
import { decodeSymbolParam } from "@/lib/symbolEncoding";

/** BFF proxy: generate DPS insights (LLM).
 *  Mirrors POST /api/symbols/{symbol}/positions/{positionId}/dps-insights. */
export async function POST(
  _req: Request,
  { params }: { params: Promise<{ symbol: string; positionId: string }> },
) {
  const { symbol: _rawSym, positionId } = await params;
  const symbol = decodeSymbolParam(_rawSym);
  try {
    const res = await fetch(
      `${API_BASE_URL}/api/symbols/${encodeURIComponent(symbol)}/positions/${encodeURIComponent(positionId)}/dps-insights`,
      { method: "POST", headers: { Accept: "application/json" } },
    );
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Upstream API error" },
      { status: 502 },
    );
  }
}
