/** Frozen TypeScript types matching the portfolio contract v1.1 */

// ─── Enums ───────────────────────────────────────────────────────────────────

export type TxnType = "BUY" | "SELL" | "DIVIDEND";
export type CostBasisStatus = "COMPLETE" | "INCOMPLETE";
export type WarningType =
  | "NEGATIVE_INVENTORY"
  | "ZERO_COST_ACQUISITION"
  | "RIGHTS_AMOUNT"
  | "PROBABLE_DUPLICATE";
export type AssetClass = "Equity";
export type SecurityStatus = "ACTIVE" | "DELISTED";
export type FxRateSource = "ECB" | "BROKER" | "MANUAL";
export type ImportSource = "csv_import" | "manual";

// ─── Security Master ─────────────────────────────────────────────────────────

export interface SecurityAlias {
  source: string;
  value: string;
  normalized?: string;
}

export interface SecurityMaster {
  security_id: string;         // "XNYS:AAPL"
  ticker: string;
  company_name: string;
  exchange_mic: string;
  asset_class: AssetClass;
  listing_currency: string;    // ISO 4217
  isin?: string;
  cusip?: string;
  sedol?: string;
  aliases?: SecurityAlias[];
  broker_ids?: Record<string, string>;
  status: SecurityStatus;
  created_at?: string;
  updated_at?: string;
}

export interface CreateSecurityRequest {
  ticker: string;
  company_name: string;
  exchange_mic: string;
  asset_class?: AssetClass;
  listing_currency: string;
  isin?: string;
  cusip?: string;
  sedol?: string;
  aliases?: SecurityAlias[];
}

export interface SecuritiesResponse {
  securities: SecurityMaster[];
}

// ─── Movement Warning ────────────────────────────────────────────────────────

export interface MovementWarning {
  type: WarningType;
  message: string;
  security?: string;
  security_id?: string;
  shares?: string;
  row_index?: number;
  count?: number;
  amount?: string;
  row_indices?: number[];
  existing_movement_id?: string;
  company?: string;
}

// ─── Ledger Movement ─────────────────────────────────────────────────────────

export interface MoneyAmount {
  amount: string;
  currency: string;
  eur_amount: string;
}

export interface FeeAmount {
  total: string;
  currency: string;
  total_eur: string;
}

export interface WithholdingLeg {
  country: string;
  rate_pct: string;
  amount_eur: string;
}

export interface Withholding {
  source?: WithholdingLeg | null;
  destination?: WithholdingLeg | null;
}

export interface FxInfo {
  rate: string;
  rate_source: FxRateSource;
}

export interface LedgerMovement {
  id: string;
  txn_type: TxnType;
  security_id: string;
  ticker: string;
  company_name: string;
  trade_date: string;           // "YYYY-MM-DD"
  quantity: string | null;      // null for DIVIDEND (no share count in source schema)
  gross: MoneyAmount;
  fees: FeeAmount;
  withholding: Withholding;
  net: MoneyAmount;
  fx: FxInfo;
  account_id: string;
  import_source: ImportSource;
  created_at: string;
  cost_basis_status?: CostBasisStatus;
  source_derechos_amount?: string;
  warnings?: MovementWarning[];
}

export interface MovementsResponse {
  movements: LedgerMovement[];
  total_count: number;
  limit: number;
  offset: number;
}

// ─── Holdings ────────────────────────────────────────────────────────────────

export interface HoldingEntry {
  security_id: string;
  ticker: string;
  company_name: string;
  total_shares: string;
  avg_cost_basis_eur: string | null;
  cost_basis_status: CostBasisStatus;
  total_invested_eur: string;
  total_dividends_eur: string;
  accounts: string[];
  warnings: MovementWarning[];
}

export interface HoldingsSummary {
  total_securities: number;
  total_invested_eur: string;
  total_dividends_eur: string;
}

export interface HoldingsResponse {
  holdings: HoldingEntry[];
  summary: HoldingsSummary;
}
