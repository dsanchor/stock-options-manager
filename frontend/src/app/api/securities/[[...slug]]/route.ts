import { NextResponse } from "next/server";
import { API_BASE_URL } from "@/lib/api";
import type { NextRequest } from "next/server";

/**
 * BFF proxy: browser → this Next route → internal Python API.
 * Handles GET /api/securities, POST /api/securities, GET /api/securities/{id}.
 * Uses optional catch-all [[...slug]] so it matches both base and sub-paths.
 */
async function proxy(req: NextRequest): Promise<NextResponse> {
  const path = req.nextUrl.pathname;
  const qs = req.nextUrl.search;
  const url = `${API_BASE_URL}${path}${qs}`;

  const headers: Record<string, string> = { Accept: "application/json" };
  const ct = req.headers.get("content-type");
  if (ct) headers["content-type"] = ct;

  let body: ArrayBuffer | undefined;
  if (!["GET", "HEAD"].includes(req.method)) {
    body = await req.arrayBuffer();
  }

  try {
    const res = await fetch(url, { method: req.method, headers, body });
    const data = await res.json().catch(() => ({}));
    return NextResponse.json(data, { status: res.status });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Upstream error" },
      { status: 502 },
    );
  }
}

export const GET = (req: NextRequest) => proxy(req);
export const POST = (req: NextRequest) => proxy(req);
export const PUT = (req: NextRequest) => proxy(req);
export const DELETE = (req: NextRequest) => proxy(req);
