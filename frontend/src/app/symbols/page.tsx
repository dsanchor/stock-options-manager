import { apiFetch } from "@/lib/api";
import { usd, timeAgo } from "@/lib/format";
import SymbolsTable from "@/components/SymbolsTable";
import AddSymbolForm from "@/components/AddSymbolForm";
import type { SymbolsOverview, SymbolRow, PortfolioSummary } from "@/types/symbols";

export const dynamic = "force-dynamic";

async function getData(): Promise<SymbolsOverview> {
  try {
    // Fetch the inclusive dataset once (auto-enrolled zero-share "historical"
    // rows included) so the "Hide historical (0 shares)" toggle in
    // SymbolsTable can reveal them entirely client-side, with no refetch.
    // Without `include_zero_portfolio=true`, the backend hides those rows
    // unconditionally and unchecking the toggle would have nothing to reveal.
    // See livingston-unified-watchlist-api-contract.md.
    return await apiFetch<SymbolsOverview>("/api/symbols/overview?include_zero_portfolio=true");
  } catch (err) {
    return { error: err instanceof Error ? err.message : "Failed to load symbols" };
  }
}

/** Format a EUR number (or string) for KPI display. */
function kpiEur(v: number | string | null | undefined): string {
  const n = typeof v === "string" ? parseFloat(v) : (v ?? NaN);
  if (!isFinite(n)) return "—";
  return new Intl.NumberFormat("de-DE", {
    style: "currency",
    currency: "EUR",
    maximumFractionDigits: 2,
  }).format(n);
}

function KpiCard({
  label,
  value,
  tone,
  tooltip,
}: {
  label: string;
  value: string;
  tone?: "green" | "red" | "neutral";
  tooltip?: string;
}) {
  const valueClass =
    tone === "green"
      ? "text-accent-green"
      : tone === "red"
        ? "text-accent-red"
        : "text-text";
  return (
    <div
      className="rounded-[var(--radius)] border border-border bg-bg-card px-4 py-3 flex-1 min-w-[160px]"
      title={tooltip}
    >
      <div className="text-xs text-text-muted mb-1">{label}</div>
      <div className={`text-base font-semibold font-mono ${valueClass}`}>{value}</div>
    </div>
  );
}

export default async function SymbolsPage() {
  const d = await getData();

  if (d.error) {
    return (
      <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm">
        ⚠️ {d.error}
      </div>
    );
  }

  // Unified list: prefer Livingston's `symbols[]`, then legacy sectioned arrays, then flat rows
  const allSymbols: SymbolRow[] =
    d.symbols ??
    (d.portfolio_rows || d.watchlist_rows
      ? [...(d.portfolio_rows ?? []), ...(d.watchlist_rows ?? [])]
      : null) ??
    d.rows ??
    [];

  const totalCount = d.symbol_count ?? allSymbols.length;

  const ps: PortfolioSummary | null | undefined = d.portfolio_summary;
  // Resolve field names across both backend contracts
  const totalInvestment = ps?.total_investment_eur ?? (ps?.remaining_cost_basis_eur != null ? parseFloat(ps.remaining_cost_basis_eur) : null);
  const netGains = ps?.net_gains_eur ?? (ps?.realized_result_eur != null ? parseFloat(ps.realized_result_eur) : null);
  const totalDividends = ps?.total_dividends_eur;
  const hasPortfolioSummary = ps != null && (totalInvestment != null || netGains != null);

  const netGainsTone =
    netGains == null ? "neutral" : netGains >= 0 ? "green" : "red";

  return (
    <div className="space-y-6">
      {/* ── Header ───────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Symbols</h1>
          {d.last_update_ts && (
            <p className="text-sm text-text-muted">Last enrichment {timeAgo(d.last_update_ts)}</p>
          )}
        </div>
        {/* Row 1 — Options exposure */}
        <div className="flex flex-wrap gap-4 text-sm text-text-muted">
          <span>{totalCount} tracked</span>
          <span>
            Calls exposure <span className="text-text">${usd(d.total_call_exposure)}</span>
          </span>
          <span>
            Puts committed <span className="text-text">${usd(d.total_put_exposure)}</span>
          </span>
        </div>
      </div>

      {/* Row 2 — Portfolio KPIs (visible only when holdings data exists) */}
      {hasPortfolioSummary && (
        <div className="space-y-2">
          <div className="flex flex-wrap gap-3">
            <KpiCard
              label="Inversión actual"
              value={kpiEur(totalInvestment)}
              tone="neutral"
              tooltip="Base de coste CMP de las acciones que aún posees (remaining_cost_basis_eur)."
            />
            <KpiCard
              label="Resultado realizado"
              value={kpiEur(netGains)}
              tone={netGainsTone}
              tooltip="Ganancia o pérdida cerrada por ventas de acciones y derechos. No incluye ganancias no realizadas ni P&L de opciones."
            />
            <KpiCard
              label="Dividendos netos"
              value={kpiEur(totalDividends)}
              tone="neutral"
              tooltip="Total de dividendos netos recibidos (tras retenciones) en todos los activos."
            />
          </div>
          {ps?.has_incomplete_cost_basis && (
            <p className="text-xs text-accent-orange">
              ⚠ Algunos valores tienen coste de adquisición incompleto — el resultado realizado puede estar subestimado.
            </p>
          )}
        </div>
      )}

      <AddSymbolForm />

      <SymbolsTable rows={allSymbols} />
    </div>
  );
}
