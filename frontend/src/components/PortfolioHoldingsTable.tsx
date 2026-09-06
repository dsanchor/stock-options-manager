"use client";

import { useState, useCallback } from "react";
import Link from "next/link";
import { getHoldings } from "@/lib/portfolio-api";
import type { HoldingEntry, HoldingsResponse, WarningType } from "@/types/portfolio";
import { useEffect } from "react";

const WARNING_SHORT: Record<WarningType, string> = {
  NEGATIVE_INVENTORY: "Negative inventory",
  ZERO_COST_ACQUISITION: "Incomplete cost basis",
  RIGHTS_AMOUNT: "Rights amount pending",
  PROBABLE_DUPLICATE: "Probable duplicate",
};

function Skeleton() {
  return (
    <div className="space-y-2">
      {[...Array(4)].map((_, i) => (
        <div key={i} className="skeleton h-12 rounded-[var(--radius)]" />
      ))}
    </div>
  );
}

/** Client-side holdings table with empty, loading, and error states. */
export default function PortfolioHoldingsTable() {
  const [data, setData] = useState<HoldingsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const d = await getHoldings();
      setData(d);
    } catch (err) {
      const e = err as { status?: number; data?: { error?: string; detail?: string } };
      if (e.status === 503) {
        setError("Portfolio storage is not yet configured. Movements will appear here once the backend is set up.");
      } else {
        setError(e.data?.detail ?? (err instanceof Error ? err.message : "Failed to load holdings"));
      }
    } finally {
      setLoading(false);
    }
  }, []);

  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { load(); }, [load]);

  if (loading) return <Skeleton />;

  if (error) {
    return (
      <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm">
        ⚠️ {error}
      </div>
    );
  }

  if (!data || data.holdings.length === 0) {
    return (
      <div className="rounded-[var(--radius-card)] border border-border bg-bg-card p-10 text-center space-y-4">
        <div className="text-4xl">📂</div>
        <div className="text-base font-medium text-text">No holdings yet</div>
        <div className="text-sm text-text-muted">
          Holdings are derived from committed ledger movements.
          <br />Import your first CSV to get started.
        </div>
        <Link
          href="/portfolio/import"
          className="inline-flex items-center gap-2 rounded-[var(--radius)] bg-[image:var(--grad-blue)] px-5 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          Import CSV
        </Link>
      </div>
    );
  }

  const { holdings, summary } = data;

  return (
    <div className="space-y-5">
      {/* Summary bar */}
      <div className="flex flex-wrap gap-6 rounded-[var(--radius)] border border-border bg-bg-card px-5 py-4 text-sm">
        <div>
          <div className="text-xs text-text-muted mb-1">Securities</div>
          <div className="text-lg font-semibold text-text">{summary.total_securities}</div>
        </div>
        <div>
          <div className="text-xs text-text-muted mb-1">Total invested</div>
          <div className="text-lg font-semibold text-text">
            €{Number(summary.total_invested_eur).toLocaleString("es-ES", { minimumFractionDigits: 2 })}
          </div>
        </div>
        <div>
          <div className="text-xs text-text-muted mb-1">Total dividends</div>
          <div className="text-lg font-semibold text-accent-green">
            €{Number(summary.total_dividends_eur).toLocaleString("es-ES", { minimumFractionDigits: 2 })}
          </div>
        </div>
        <div className="ml-auto">
          <button
            type="button"
            onClick={load}
            className="rounded-[var(--radius-pill)] border border-border px-3 py-1.5 text-xs text-text-muted hover:bg-bg-hover"
          >
            Refresh
          </button>
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto rounded-[var(--radius)] border border-border">
        <table className="w-full table-modern text-sm">
          <thead>
            <tr className="border-b border-border bg-bg-card/80">
              {["Security", "Shares", "Avg Cost (€)", "Invested (€)", "Dividends (€)", "Accounts", ""].map(
                (h, i) => (
                  <th
                    key={i}
                    className={`px-4 py-2.5 text-xs font-semibold uppercase tracking-wide text-text-muted ${
                      i >= 1 && i <= 4 ? "text-right" : "text-left"
                    }`}
                  >
                    {h}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody className="divide-y divide-border/40">
            {holdings.map((h) => (
              <HoldingRow key={h.security_id} holding={h} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function HoldingRow({ holding: h }: { holding: HoldingEntry }) {
  const [showWarnings, setShowWarnings] = useState(false);
  const shares = parseFloat(h.total_shares);
  const isNegative = shares < 0;

  return (
    <>
      <tr>
        <td className="px-4 py-3">
          <div className="font-mono font-semibold text-text">{h.ticker}</div>
          <div className="text-xs text-text-muted truncate max-w-[180px]">{h.company_name}</div>
        </td>
        <td className={`px-4 py-3 text-right font-mono ${isNegative ? "text-accent-red" : "text-text"}`}>
          {Number(h.total_shares).toLocaleString("es-ES", { maximumFractionDigits: 6 })}
        </td>
        <td className="px-4 py-3 text-right font-mono text-text">
          {h.avg_cost_basis_eur != null
            ? `€${Number(h.avg_cost_basis_eur).toLocaleString("es-ES", { minimumFractionDigits: 2 })}`
            : <span className="text-text-muted text-xs">Incomplete</span>}
        </td>
        <td className="px-4 py-3 text-right font-mono text-text">
          €{Number(h.total_invested_eur).toLocaleString("es-ES", { minimumFractionDigits: 2 })}
        </td>
        <td className="px-4 py-3 text-right font-mono text-accent-green">
          €{Number(h.total_dividends_eur).toLocaleString("es-ES", { minimumFractionDigits: 2 })}
        </td>
        <td className="px-4 py-3">
          <div className="flex flex-wrap gap-1">
            {h.accounts.map((a) => (
              <span
                key={a}
                className="rounded-full bg-bg-hover px-2 py-0.5 text-xs text-text-muted"
              >
                {a === "_unassigned" ? "—" : a}
              </span>
            ))}
          </div>
        </td>
        <td className="px-3 py-3 text-right">
          {h.warnings.length > 0 && (
            <button
              type="button"
              onClick={() => setShowWarnings((v) => !v)}
              title="View warnings"
              className="text-accent-orange hover:text-accent-orange/70 transition-colors"
            >
              ⚠
            </button>
          )}
        </td>
      </tr>
      {showWarnings && h.warnings.length > 0 && (
        <tr>
          <td colSpan={7} className="px-4 pb-3 pt-0">
            <div className="rounded-[var(--radius)] border border-accent-orange/30 bg-accent-orange/5 px-4 py-2 space-y-1">
              {h.warnings.map((w, i) => (
                <div key={i} className="flex items-start gap-2 text-xs text-text-muted">
                  <span className="text-accent-orange mt-0.5 shrink-0">⚠</span>
                  <span>
                    <span className="font-medium text-text mr-1">
                      {WARNING_SHORT[w.type as WarningType] ?? w.type}:
                    </span>
                    {w.message}
                  </span>
                </div>
              ))}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
