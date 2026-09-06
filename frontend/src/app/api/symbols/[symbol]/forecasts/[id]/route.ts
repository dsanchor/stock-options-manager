import { NextResponse } from "next/server";
import { apiFetch } from "@/lib/api";
import { decodeSymbolParam } from "@/lib/symbolEncoding";

/** BFF proxy: single forecast detail. Mirrors GET /api/symbols/{symbol}/forecasts/{id}. */
export async function GET(
  _req: Request,
  { params }: { params: Promise<{ symbol: string; id: string }> },
) {
  const { symbol: _rawSym, id } = await params;
  const symbol = decodeSymbolParam(_rawSym);
  try {
    const data = await apiFetch<unknown>(
      `/api/symbols/${encodeURIComponent(symbol)}/forecasts/${encodeURIComponent(id)}`,
    );
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Upstream API error" },
      { status: 502 },
    );
  }
}
