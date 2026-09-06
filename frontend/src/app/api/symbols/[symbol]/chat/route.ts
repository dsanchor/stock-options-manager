import { NextResponse } from "next/server";
import { apiFetch } from "@/lib/api";
import { decodeSymbolParam } from "@/lib/symbolEncoding";

/**
 * BFF proxy for per-symbol chat completion. Mirrors POST /api/symbols/{symbol}/chat.
 */
export async function POST(
  req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol: _rawSym } = await params;
  const symbol = decodeSymbolParam(_rawSym);
  try {
    const body = await req.json().catch(() => ({}));
    const data = await apiFetch<unknown>(`/api/symbols/${encodeURIComponent(symbol)}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body ?? {}),
    });
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Upstream API error" },
      { status: 502 },
    );
  }
}
