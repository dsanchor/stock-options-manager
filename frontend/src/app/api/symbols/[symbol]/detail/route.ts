import { NextResponse } from "next/server";
import { apiFetch } from "@/lib/api";
import { decodeSymbolParam } from "@/lib/symbolEncoding";

/** BFF proxy: full symbol detail. Mirrors GET /api/symbols/{symbol}/detail. */
export async function GET(
  _req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol: _rawSym } = await params;
  const symbol = decodeSymbolParam(_rawSym);
  try {
    const data = await apiFetch<unknown>(`/api/symbols/${encodeURIComponent(symbol)}/detail`);
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Upstream API error" },
      { status: 502 },
    );
  }
}
