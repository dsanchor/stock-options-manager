import { NextResponse } from "next/server";
import { API_BASE_URL } from "@/lib/api";
import type { NextRequest } from "next/server";

/**
 * BFF proxy: POST /api/symbols/add
 * Unified add-symbol flow (select existing SecurityMaster or create new).
 * Forwards to the backend POST /api/symbols/add and relays the response.
 */
export async function POST(req: NextRequest): Promise<NextResponse> {
  try {
    const body = await req.text();
    const res = await fetch(`${API_BASE_URL}/api/symbols/add`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body,
    });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Upstream API error" },
      { status: 502 },
    );
  }
}
