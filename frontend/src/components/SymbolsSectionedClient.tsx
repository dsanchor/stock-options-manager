"use client";

import { useState } from "react";
import SymbolsTable from "@/components/SymbolsTable";
import type { SymbolRow } from "@/types/symbols";

interface Props {
  portfolioRows: SymbolRow[];
  watchlistRows: SymbolRow[];
  portfolioCount: number;
  watchlistCount: number;
}

export default function SymbolsSectionedClient({
  portfolioRows,
  watchlistRows,
  portfolioCount,
  watchlistCount,
}: Props) {
  const [portfolioOpen, setPortfolioOpen] = useState(true);
  const [watchlistOpen, setWatchlistOpen] = useState(true);

  return (
    <div className="space-y-6">
      {/* Portfolio section */}
      <section aria-label="Portfolio symbols">
        <SectionHeader
          title="Portfolio"
          count={portfolioCount}
          open={portfolioOpen}
          onToggle={() => setPortfolioOpen((v) => !v)}
        />
        {portfolioOpen && (
          portfolioRows.length === 0 ? (
            <p className="mt-3 text-sm text-text-muted pl-1">
              No portfolio symbols. Import movements to see securities here.
            </p>
          ) : (
            <div className="mt-3">
              <SymbolsTable rows={portfolioRows} listSection="portfolio" />
            </div>
          )
        )}
      </section>

      {/* Watchlist section */}
      <section aria-label="Watchlist symbols">
        <SectionHeader
          title="Watchlist"
          count={watchlistCount}
          open={watchlistOpen}
          onToggle={() => setWatchlistOpen((v) => !v)}
        />
        {watchlistOpen && (
          watchlistRows.length === 0 ? (
            <p className="mt-3 text-sm text-text-muted pl-1">
              No watchlist-only symbols. Add a symbol to start tracking.
            </p>
          ) : (
            <div className="mt-3">
              <SymbolsTable rows={watchlistRows} listSection="watchlist" />
            </div>
          )
        )}
      </section>
    </div>
  );
}

function SectionHeader({
  title,
  count,
  open,
  onToggle,
}: {
  title: string;
  count: number;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className="flex w-full items-center gap-2 rounded-[var(--radius)] px-1 py-1 text-left hover:bg-bg-hover/50 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue/60"
      aria-expanded={open}
    >
      <span className="text-sm font-semibold text-text">{title}</span>
      <span className="rounded-[var(--radius-pill)] border border-border bg-bg-input px-2 py-0.5 text-xs text-text-muted">
        {count}
      </span>
      <span className="ml-auto text-text-muted text-xs">{open ? "▲" : "▼"}</span>
    </button>
  );
}
