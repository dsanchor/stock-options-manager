/**
 * Typed client for all portfolio / securities / import proxy routes.
 * These are browser-side calls: /api/securities, /api/portfolio/*, /api/import/*.
 * All paths go through the Next.js BFF proxy to the internal Python backend.
 */

import type {
  SecurityMaster,
  CreateSecurityRequest,
  SecuritiesResponse,
  HoldingsResponse,
  MovementsResponse,
  LedgerMovement,
} from "@/types/portfolio";
import type {
  ImportSession,
  ImportAnswer,
  ImportPreviewResponse,
  CommitResult,
  UploadParams,
} from "@/types/import";

// ─── Helpers ─────────────────────────────────────────────────────────────────

interface ApiError extends Error {
  status: number;
  data: { error?: string; detail?: string; [key: string]: unknown };
}

async function fetchJSON<T>(url: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers as HeadersInit | undefined);
  headers.set("Accept", "application/json");

  const res = await fetch(url, { ...init, headers });

  if (!res.ok) {
    const data = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
    const err = new Error(data.detail ?? data.error ?? `HTTP ${res.status}`) as ApiError;
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return res.json() as Promise<T>;
}

// ─── Securities Catalog ──────────────────────────────────────────────────────

export async function listSecurities(): Promise<SecuritiesResponse> {
  return fetchJSON<SecuritiesResponse>("/api/securities");
}

export async function getSecurity(securityId: string): Promise<SecurityMaster> {
  return fetchJSON<SecurityMaster>(`/api/securities/${encodeURIComponent(securityId)}`);
}

export async function createSecurity(
  data: CreateSecurityRequest,
): Promise<SecurityMaster> {
  return fetchJSON<SecurityMaster>("/api/securities", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

// ─── Portfolio / Holdings ─────────────────────────────────────────────────────

export async function getHoldings(accountId?: string): Promise<HoldingsResponse> {
  const qs = accountId ? `?account_id=${encodeURIComponent(accountId)}` : "";
  return fetchJSON<HoldingsResponse>(`/api/portfolio/holdings${qs}`);
}

// ─── Portfolio / Movements ────────────────────────────────────────────────────

export interface MovementsFilter {
  account_id?: string;
  security_id?: string;
  txn_type?: string;
  date_from?: string;
  date_to?: string;
  limit?: number;
  offset?: number;
}

export async function getMovements(
  filter: MovementsFilter = {},
): Promise<MovementsResponse> {
  const params = new URLSearchParams();
  if (filter.account_id) params.set("account_id", filter.account_id);
  if (filter.security_id) params.set("security_id", filter.security_id);
  if (filter.txn_type) params.set("txn_type", filter.txn_type);
  if (filter.date_from) params.set("date_from", filter.date_from);
  if (filter.date_to) params.set("date_to", filter.date_to);
  if (filter.limit !== undefined) params.set("limit", String(filter.limit));
  if (filter.offset !== undefined) params.set("offset", String(filter.offset));
  const qs = params.toString() ? `?${params.toString()}` : "";
  return fetchJSON<MovementsResponse>(`/api/portfolio/movements${qs}`);
}

export async function deleteMovement(
  movementId: string,
  accountId?: string,
): Promise<Pick<LedgerMovement, "id"> & { deleted_at: string }> {
  const params = new URLSearchParams();
  if (accountId) params.set("account_id", accountId);
  const qs = params.toString() ? `?${params.toString()}` : "";
  return fetchJSON(`/api/portfolio/movements/${encodeURIComponent(movementId)}${qs}`, {
    method: "DELETE",
  });
}

// ─── Import Sessions ──────────────────────────────────────────────────────────

export async function createImportSession(
  params: UploadParams,
): Promise<ImportSession> {
  const form = new FormData();

  if (params.file) {
    form.append("file", params.file);
  } else if (params.content !== undefined) {
    const blob = new Blob([params.content], { type: "text/csv" });
    form.append("file", blob, params.filename ?? "import.csv");
  } else {
    throw new Error("Either file or content must be provided");
  }

  if (params.format_hint) form.append("format_hint", params.format_hint);
  if (params.currency) form.append("currency", params.currency);
  if (params.account_id) form.append("account_id", params.account_id);

  // Do NOT set Content-Type manually — browser sets multipart boundary automatically
  const res = await fetch("/api/import/sessions", {
    method: "POST",
    headers: { Accept: "application/json" },
    body: form,
  });

  if (!res.ok) {
    const data = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
    const err = new Error(data.detail ?? data.error ?? `HTTP ${res.status}`) as ApiError;
    (err as ApiError).status = res.status;
    (err as ApiError).data = data;
    throw err;
  }
  return res.json() as Promise<ImportSession>;
}

export async function getImportSession(sessionId: string): Promise<ImportSession> {
  return fetchJSON<ImportSession>(`/api/import/sessions/${encodeURIComponent(sessionId)}`);
}

export async function answerQuestion(
  sessionId: string,
  answer: ImportAnswer | { question_id: string; answer_type: string; selected_security_id?: string; batch_value?: string },
): Promise<ImportSession> {
  return fetchJSON<ImportSession>(
    `/api/import/sessions/${encodeURIComponent(sessionId)}/answers`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(answer),
    },
  );
}

export async function generatePreview(
  sessionId: string,
): Promise<ImportPreviewResponse> {
  return fetchJSON<ImportPreviewResponse>(
    `/api/import/sessions/${encodeURIComponent(sessionId)}/preview`,
    { method: "POST" },
  );
}

export async function commitImport(sessionId: string): Promise<CommitResult> {
  return fetchJSON<CommitResult>(
    `/api/import/sessions/${encodeURIComponent(sessionId)}/commit`,
    { method: "POST" },
  );
}
