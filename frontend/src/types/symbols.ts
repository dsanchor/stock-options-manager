export interface SymbolWatchlistFlags {
  covered_call: boolean;
  cash_secured_put: boolean;
  buy_tracker: boolean;
}

export type SymbolListSection = "portfolio" | "watchlist";

export interface SymbolRow {
  symbol: string;
  display_name: string;
  category: string;
  dgi_score: number | null;
  tech_timing: number | null;
  entry_tag: string;
  momentum: string;
  price: number | null;
  total_shares: number;
  active_count: number;
  in_calls: number;
  put_exposure: number;
  call_exposure: number;
  watchlist?: SymbolWatchlistFlags;
  // Symbol Unification rev 3 — new fields (optional: populated by backend once deployed)
  list_section?: SymbolListSection;
  security_id?: string | null;
  portfolio_shares?: string | null;
  portfolio_avg_cost_eur?: string | null;
  portfolio_invested_eur?: string | null;
}

export interface SymbolsOverview {
  rows?: SymbolRow[];
  // Symbol Unification rev 3 — sectioned rows
  portfolio_rows?: SymbolRow[];
  watchlist_rows?: SymbolRow[];
  symbol_count?: number;
  portfolio_count?: number;
  watchlist_count?: number;
  total_call_exposure?: number;
  total_put_exposure?: number;
  last_update_ts?: string;
  error?: string;
}
