import { apiFetch } from "@/lib/api";
import { usd, timeAgo } from "@/lib/format";
import SymbolsTable from "@/components/SymbolsTable";
import SymbolsSectionedClient from "@/components/SymbolsSectionedClient";
import AddSymbolForm from "@/components/AddSymbolForm";
import type { SymbolsOverview, SymbolRow } from "@/types/symbols";

export const dynamic = "force-dynamic";

async function getData(): Promise<SymbolsOverview> {
  try {
    return await apiFetch<SymbolsOverview>("/api/symbols/overview");
  } catch (err) {
    return { error: err instanceof Error ? err.message : "Failed to load symbols" };
  }
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

  // Prefer sectioned arrays from backend; fall back to flat rows for backward compat
  const portfolioRows: SymbolRow[] =
    d.portfolio_rows ?? d.rows?.filter((r) => r.list_section === "portfolio") ?? [];
  const watchlistRows: SymbolRow[] =
    d.watchlist_rows ??
    d.rows?.filter((r) => r.list_section !== "portfolio") ??
    d.rows ??
    [];

  const totalCount = d.symbol_count ?? portfolioRows.length + watchlistRows.length;
  const portfolioCount = d.portfolio_count ?? portfolioRows.length;
  const watchlistCount = d.watchlist_count ?? watchlistRows.length;

  // Use sectioned layout when backend provides the new arrays
  const useSectioned = d.portfolio_rows != null || d.watchlist_rows != null;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Symbols</h1>
          {d.last_update_ts && (
            <p className="text-sm text-text-muted">Last enrichment {timeAgo(d.last_update_ts)}</p>
          )}
        </div>
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

      <AddSymbolForm />

      {useSectioned ? (
        <SymbolsSectionedClient
          portfolioRows={portfolioRows}
          watchlistRows={watchlistRows}
          portfolioCount={portfolioCount}
          watchlistCount={watchlistCount}
        />
      ) : (
        // Legacy flat layout — backward compat until backend ships sectioned response
        <SymbolsTable rows={d.rows ?? []} />
      )}
    </div>
  );
}
