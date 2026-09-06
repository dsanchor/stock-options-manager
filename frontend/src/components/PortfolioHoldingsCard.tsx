import type { PortfolioSection, SymbolState } from "@/types/symbol-detail";

interface Props {
  portfolio: PortfolioSection;
  symbolState: SymbolState | null | undefined;
}

function eur(v: string | null | undefined): string {
  if (!v) return "—";
  const n = parseFloat(v);
  if (!isFinite(n)) return "—";
  return new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR", maximumFractionDigits: 2 }).format(n);
}

function shares(v: string | null | undefined): string {
  if (!v) return "—";
  const n = parseFloat(v);
  if (!isFinite(n)) return "—";
  return n % 1 === 0 ? n.toFixed(0) : n.toFixed(6).replace(/0+$/, "");
}

export default function PortfolioHoldingsCard({ portfolio, symbolState }: Props) {
  const isHistorical =
    symbolState === "portfolio_historical" ||
    (parseFloat(portfolio.current_shares) === 0 && symbolState !== "watchlist_and_portfolio");
  const sharesStr = shares(portfolio.current_shares);

  return (
    <div className="surface rounded-[var(--radius)] border border-border p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-text">Portfolio Holdings</h3>
        {isHistorical && (
          <span className="inline-block rounded-[var(--radius-pill)] border border-border px-2 py-0.5 text-xs text-text-muted">
            Historical — 0 shares
          </span>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <Stat label="Shares" value={isHistorical ? "0 (historical)" : sharesStr} />
        <Stat label="Avg Cost" value={eur(portfolio.average_cost_eur)} />
        <Stat label="Invested" value={eur(portfolio.current_invested_eur)} />
        {portfolio.total_dividends_eur && (
          <Stat label="Dividends" value={eur(portfolio.total_dividends_eur)} />
        )}
      </div>

      {portfolio.holdings_by_account && portfolio.holdings_by_account.length > 0 && (
        <div>
          <p className="mb-1.5 text-xs font-medium text-text-muted">By account</p>
          <div className="divide-y divide-border/50 rounded-[var(--radius)] border border-border/60">
            {portfolio.holdings_by_account.map((acct) => (
              <div key={acct.account_id} className="flex items-center justify-between px-3 py-2 text-sm">
                <span className="text-text">{acct.account_name ?? acct.account_id}</span>
                <div className="flex items-center gap-4 text-right text-text-muted">
                  <span>{shares(acct.shares)} shares</span>
                  {acct.avg_cost_eur && <span>{eur(acct.avg_cost_eur)} avg</span>}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs text-text-muted">{label}</p>
      <p className="mt-0.5 font-mono text-sm text-text">{value}</p>
    </div>
  );
}
