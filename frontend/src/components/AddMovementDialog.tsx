"use client";

import { useEffect, useState, useCallback } from "react";
import { X, RefreshCw } from "lucide-react";
import { createMovement, createTransfer, getFxRate, listAccounts, listSecurities } from "@/lib/portfolio-api";
import type { BrokerAccount, ManualMovementRequest, TransferRequest } from "@/types/portfolio";
import type { SecurityMaster } from "@/types/portfolio";

const inputCls =
  "w-full rounded-[var(--radius)] border border-border bg-bg-input px-3 py-2 text-sm text-text placeholder:text-text-muted focus:border-accent-blue focus:outline-none";
const labelCls = "mb-1 block text-xs font-medium text-text-muted";

/** UI-only type selector — Transfer is NOT a TxnType enum but a separate endpoint. */
type UiMovementType = "BUY" | "SELL" | "DIVIDEND" | "TRANSFER";

const TXN_TYPES: Array<{ value: UiMovementType; label: string; description: string }> = [
  { value: "BUY", label: "Buy", description: "Purchase shares or other securities" },
  { value: "SELL", label: "Sell", description: "Sell shares — choose ACCIONES or DERECHOS" },
  { value: "DIVIDEND", label: "Dividend", description: "Cash dividend received" },
  { value: "TRANSFER", label: "Transfer", description: "Move shares between accounts" },
];

// ─── FX Helper ────────────────────────────────────────────────────────────────

interface FxHelperProps {
  currency: string;
  date: string;
  onRate: (rate: string) => void;
}

function FxHelper({ currency, date, onRate }: FxHelperProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rate, setRate] = useState<string | null>(null);

  const fetch = useCallback(async () => {
    if (!currency || currency === "EUR" || !date) return;
    setLoading(true);
    setError(null);
    try {
      const result = await getFxRate(currency, "EUR", date);
      setRate(result.rate);
      onRate(result.rate);
    } catch {
      setError("FX rate unavailable");
    } finally {
      setLoading(false);
    }
  }, [currency, date, onRate]);

  if (!currency || currency === "EUR") return null;

  return (
    <div className="flex items-center gap-2 text-xs text-text-muted">
      {rate ? (
        <span className="text-accent-green">
          1 {currency} = {rate} EUR
          {" "}
          <button type="button" onClick={fetch} className="underline hover:text-text" title="Refresh FX rate">
            <RefreshCw size={10} className="inline" />
          </button>
        </span>
      ) : (
        <button
          type="button"
          onClick={fetch}
          disabled={loading || !date}
          className="underline hover:text-text disabled:opacity-40"
        >
          {loading ? "Fetching rate…" : `Get ${currency}/EUR rate`}
        </button>
      )}
      {error && <span className="text-accent-red">{error}</span>}
    </div>
  );
}

// ─── Account Select Helper ────────────────────────────────────────────────────

function AccountSelect({
  value,
  onChange,
  accounts,
  label,
  required = false,
  placeholder = "Sin asignar",
}: {
  value: string;
  onChange: (v: string) => void;
  accounts: BrokerAccount[];
  label: string;
  required?: boolean;
  placeholder?: string;
}) {
  return (
    <div>
      <label className={labelCls}>{label}{required ? " *" : ""}</label>
      <select value={value} onChange={(e) => onChange(e.target.value)} className={inputCls} required={required}>
        <option value="_unassigned">{placeholder}</option>
        {accounts.map((a) => (
          <option key={a.account_id} value={a.account_id}>{a.name}</option>
        ))}
      </select>
    </div>
  );
}

// ─── Security Select Helper ────────────────────────────────────────────────────

function SecuritySelect({
  value,
  onChange,
  securities,
}: {
  value: string;
  onChange: (v: string) => void;
  securities: SecurityMaster[];
}) {
  return (
    <div>
      <label className={labelCls}>Security *</label>
      <select value={value} onChange={(e) => onChange(e.target.value)} className={inputCls} required>
        <option value="">— Select security —</option>
        {securities.map((s) => (
          <option key={s.security_id} value={s.security_id}>
            {s.ticker} — {s.company_name}
          </option>
        ))}
      </select>
    </div>
  );
}

// ─── BUY Form ─────────────────────────────────────────────────────────────────

interface BuyFormProps {
  form: BuyFormState;
  onChange: (f: Partial<BuyFormState>) => void;
  accounts: BrokerAccount[];
  securities: SecurityMaster[];
}

interface BuyFormState {
  security_id: string;
  account_id: string;
  trade_date: string;
  quantity: string;
  price_per_share: string;
  total_cost: string;
  currency: string;
  fees: string;
  notes: string;
}

function BuyForm({ form, onChange, accounts, securities }: BuyFormProps) {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      <div className="sm:col-span-2">
        <SecuritySelect value={form.security_id} onChange={(v) => onChange({ security_id: v })} securities={securities} />
      </div>
      <AccountSelect value={form.account_id} onChange={(v) => onChange({ account_id: v })} accounts={accounts} label="Account" />
      <div>
        <label className={labelCls}>Trade date *</label>
        <input type="date" value={form.trade_date} onChange={(e) => onChange({ trade_date: e.target.value })} className={inputCls} required />
      </div>
      <div>
        <label className={labelCls}>Quantity *</label>
        <input type="number" step="any" min="0" value={form.quantity} onChange={(e) => onChange({ quantity: e.target.value })} placeholder="Shares" className={inputCls} required />
      </div>
      <div>
        <label className={labelCls}>Price per share</label>
        <input type="number" step="any" min="0" value={form.price_per_share} onChange={(e) => onChange({ price_per_share: e.target.value })} placeholder="0.00 (zero for corporate actions)" className={inputCls} />
      </div>
      <div>
        <label className={labelCls}>Total cost</label>
        <input type="number" step="any" min="0" value={form.total_cost} onChange={(e) => onChange({ total_cost: e.target.value })} placeholder="0.00" className={inputCls} />
      </div>
      <div>
        <label className={labelCls}>Currency</label>
        <input type="text" value={form.currency} onChange={(e) => onChange({ currency: e.target.value.toUpperCase() })} maxLength={3} placeholder="EUR" className={inputCls} />
      </div>
      <FxHelper currency={form.currency} date={form.trade_date} onRate={(rate) => onChange({ price_per_share: rate })} />
      <div>
        <label className={labelCls}>Fees</label>
        <input type="number" step="any" min="0" value={form.fees} onChange={(e) => onChange({ fees: e.target.value })} placeholder="0.00" className={inputCls} />
      </div>
      <div className="sm:col-span-2">
        <label className={labelCls}>Notes</label>
        <textarea value={form.notes} onChange={(e) => onChange({ notes: e.target.value })} rows={2} className={`${inputCls} resize-none`} />
      </div>
    </div>
  );
}

// ─── SELL Form ────────────────────────────────────────────────────────────────

interface SellFormState {
  security_id: string;
  account_id: string;
  trade_date: string;
  quantity: string;
  sales_type: "ACCIONES" | "DERECHOS";
  price_per_share: string;
  total_proceeds: string;
  currency: string;
  fees: string;
  notes: string;
}

interface SellFormProps {
  form: SellFormState;
  onChange: (f: Partial<SellFormState>) => void;
  accounts: BrokerAccount[];
  securities: SecurityMaster[];
}

function SellForm({ form, onChange, accounts, securities }: SellFormProps) {
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <SecuritySelect value={form.security_id} onChange={(v) => onChange({ security_id: v })} securities={securities} />
        </div>
        <AccountSelect value={form.account_id} onChange={(v) => onChange({ account_id: v })} accounts={accounts} label="Account" />
        <div>
          <label className={labelCls}>Trade date *</label>
          <input type="date" value={form.trade_date} onChange={(e) => onChange({ trade_date: e.target.value })} className={inputCls} required />
        </div>
      </div>

      {/* Sale type */}
      <div>
        <label className={labelCls}>Sale type *</label>
        <div className="flex gap-3">
          {(["ACCIONES", "DERECHOS"] as const).map((t) => (
            <label key={t} className="flex items-center gap-2 cursor-pointer select-none">
              <input
                type="radio"
                name="sales_type"
                value={t}
                checked={form.sales_type === t}
                onChange={() => onChange({ sales_type: t })}
                className="accent-accent-blue"
              />
              <span className="text-sm text-text">{t}</span>
            </label>
          ))}
        </div>
        {form.sales_type === "DERECHOS" && (
          <div className="mt-2 rounded-[var(--radius)] border border-accent-blue/20 bg-accent-blue/5 px-3 py-2 text-xs text-text-muted">
            ℹ <strong className="text-text">Rights (Derechos) sale:</strong> proceeds are recorded but{" "}
            <strong className="text-text">share quantity is NOT reduced</strong>. Rights entitlements are separate from ordinary share ownership and do not affect your holdings count.
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <label className={labelCls}>Quantity{form.sales_type === "DERECHOS" ? " (rights units, optional)" : " *"}</label>
          <input
            type="number"
            step="any"
            min="0"
            value={form.quantity}
            onChange={(e) => onChange({ quantity: e.target.value })}
            placeholder="Shares / rights"
            className={inputCls}
            required={form.sales_type === "ACCIONES"}
          />
        </div>
        <div>
          <label className={labelCls}>Price per share</label>
          <input type="number" step="any" min="0" value={form.price_per_share} onChange={(e) => onChange({ price_per_share: e.target.value })} placeholder="0.00" className={inputCls} />
        </div>
        <div>
          <label className={labelCls}>Total proceeds *</label>
          <input type="number" step="any" min="0" value={form.total_proceeds} onChange={(e) => onChange({ total_proceeds: e.target.value })} placeholder="0.00" className={inputCls} required />
        </div>
        <div>
          <label className={labelCls}>Currency</label>
          <input type="text" value={form.currency} onChange={(e) => onChange({ currency: e.target.value.toUpperCase() })} maxLength={3} placeholder="EUR" className={inputCls} />
        </div>
        <FxHelper currency={form.currency} date={form.trade_date} onRate={() => {}} />
        <div>
          <label className={labelCls}>Fees</label>
          <input type="number" step="any" min="0" value={form.fees} onChange={(e) => onChange({ fees: e.target.value })} placeholder="0.00" className={inputCls} />
        </div>
        <div className="sm:col-span-2">
          <label className={labelCls}>Notes</label>
          <textarea value={form.notes} onChange={(e) => onChange({ notes: e.target.value })} rows={2} className={`${inputCls} resize-none`} />
        </div>
      </div>
    </div>
  );
}

// ─── DIVIDEND Form ────────────────────────────────────────────────────────────

interface DividendFormState {
  security_id: string;
  account_id: string;
  trade_date: string;
  gross_amount: string;
  currency: string;
  wht_source_country: string;
  wht_source_rate: string;
  wht_source_amount: string;
  wht_dest_country: string;
  wht_dest_amount: string;
  notes: string;
}

interface DividendFormProps {
  form: DividendFormState;
  onChange: (f: Partial<DividendFormState>) => void;
  accounts: BrokerAccount[];
  securities: SecurityMaster[];
}

function DividendForm({ form, onChange, accounts, securities }: DividendFormProps) {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      <div className="sm:col-span-2">
        <SecuritySelect value={form.security_id} onChange={(v) => onChange({ security_id: v })} securities={securities} />
      </div>
      <AccountSelect value={form.account_id} onChange={(v) => onChange({ account_id: v })} accounts={accounts} label="Account" />
      <div>
        <label className={labelCls}>Payment date *</label>
        <input type="date" value={form.trade_date} onChange={(e) => onChange({ trade_date: e.target.value })} className={inputCls} required />
      </div>
      <div>
        <label className={labelCls}>Gross amount *</label>
        <input type="number" step="any" min="0" value={form.gross_amount} onChange={(e) => onChange({ gross_amount: e.target.value })} placeholder="0.00" className={inputCls} required />
      </div>
      <div>
        <label className={labelCls}>Currency</label>
        <input type="text" value={form.currency} onChange={(e) => onChange({ currency: e.target.value.toUpperCase() })} maxLength={3} placeholder="EUR" className={inputCls} />
      </div>
      <FxHelper currency={form.currency} date={form.trade_date} onRate={() => {}} />

      <div className="sm:col-span-2">
        <div className="text-xs font-semibold uppercase tracking-wide text-text-muted mb-2">Withholding (optional)</div>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div>
            <label className={labelCls}>Source country</label>
            <input type="text" value={form.wht_source_country} onChange={(e) => onChange({ wht_source_country: e.target.value.toUpperCase() })} maxLength={2} placeholder="US" className={inputCls} />
          </div>
          <div>
            <label className={labelCls}>WHT rate %</label>
            <input type="number" step="any" min="0" max="100" value={form.wht_source_rate} onChange={(e) => onChange({ wht_source_rate: e.target.value })} placeholder="15.00" className={inputCls} />
          </div>
          <div>
            <label className={labelCls}>WHT amount (€)</label>
            <input type="number" step="any" min="0" value={form.wht_source_amount} onChange={(e) => onChange({ wht_source_amount: e.target.value })} placeholder="0.00" className={inputCls} />
          </div>
        </div>
      </div>

      <div className="sm:col-span-2">
        <label className={labelCls}>Notes</label>
        <textarea value={form.notes} onChange={(e) => onChange({ notes: e.target.value })} rows={2} className={`${inputCls} resize-none`} />
      </div>
    </div>
  );
}

// ─── TRANSFER Form ────────────────────────────────────────────────────────────

interface TransferFormState {
  security_id: string;
  source_account_id: string;
  dest_account_id: string;
  trade_date: string;
  quantity: string;
  carried_cost_basis: string;
  transfer_fees: string;
  notes: string;
}

interface TransferFormProps {
  form: TransferFormState;
  onChange: (f: Partial<TransferFormState>) => void;
  accounts: BrokerAccount[];
  securities: SecurityMaster[];
}

function TransferForm({ form, onChange, accounts, securities }: TransferFormProps) {
  const sameAccount = form.source_account_id && form.dest_account_id && form.source_account_id === form.dest_account_id;

  return (
    <div className="space-y-3">
      <div className="rounded-[var(--radius)] border border-accent-blue/20 bg-accent-blue/5 px-3 py-2 text-xs text-text-muted">
        ℹ Transfer moves shares between your accounts. The <strong className="text-text">carried cost basis</strong> is automatically derived from the source account's holdings (editable). Transfer fees are recorded separately and do not change the cost basis.
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <SecuritySelect value={form.security_id} onChange={(v) => onChange({ security_id: v })} securities={securities} />
        </div>
        <AccountSelect
          value={form.source_account_id}
          onChange={(v) => onChange({ source_account_id: v })}
          accounts={accounts}
          label="Source account *"
          required
          placeholder="Sin asignar"
        />
        <AccountSelect
          value={form.dest_account_id}
          onChange={(v) => onChange({ dest_account_id: v })}
          accounts={accounts}
          label="Destination account *"
          required
          placeholder="Sin asignar"
        />
        {sameAccount && (
          <div className="sm:col-span-2 rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-3 py-2 text-xs text-accent-red">
            Source and destination accounts cannot be the same.
          </div>
        )}
        <div>
          <label className={labelCls}>Transfer date *</label>
          <input type="date" value={form.trade_date} onChange={(e) => onChange({ trade_date: e.target.value })} className={inputCls} required />
        </div>
        <div>
          <label className={labelCls}>Quantity *</label>
          <input type="number" step="any" min="0" value={form.quantity} onChange={(e) => onChange({ quantity: e.target.value })} placeholder="Shares" className={inputCls} required />
        </div>
        <div>
          <label className={labelCls}>Carried cost basis (€, auto-derived)</label>
          <input type="number" step="any" min="0" value={form.carried_cost_basis} onChange={(e) => onChange({ carried_cost_basis: e.target.value })} placeholder="Auto-derived from source" className={inputCls} />
          <div className="mt-1 text-xs text-text-muted">Leave blank to derive automatically from source account holdings.</div>
        </div>
        <div>
          <label className={labelCls}>Transfer fees (€)</label>
          <input type="number" step="any" min="0" value={form.transfer_fees} onChange={(e) => onChange({ transfer_fees: e.target.value })} placeholder="0.00 (stored separately)" className={inputCls} />
          <div className="mt-1 text-xs text-text-muted">Stored separately; does not affect acquisition cost basis.</div>
        </div>
        <div className="sm:col-span-2">
          <label className={labelCls}>Notes</label>
          <textarea value={form.notes} onChange={(e) => onChange({ notes: e.target.value })} rows={2} className={`${inputCls} resize-none`} />
        </div>
      </div>
    </div>
  );
}

// ─── Main AddMovementDialog ───────────────────────────────────────────────────

export interface AddMovementDialogProps {
  onClose: () => void;
  onCreated: () => void;
}

export default function AddMovementDialog({ onClose, onCreated }: AddMovementDialogProps) {
  const [txnType, setTxnType] = useState<UiMovementType>("BUY");
  const [accounts, setAccounts] = useState<BrokerAccount[]>([]);
  const [securities, setSecurities] = useState<SecurityMaster[]>([]);
  const [loadingResources, setLoadingResources] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  // Form states
  const [buyForm, setBuyForm] = useState<BuyFormState>({
    security_id: "", account_id: "_unassigned", trade_date: "", quantity: "",
    price_per_share: "", total_cost: "", currency: "EUR", fees: "", notes: "",
  });
  const [sellForm, setSellForm] = useState<SellFormState>({
    security_id: "", account_id: "_unassigned", trade_date: "", quantity: "",
    sales_type: "ACCIONES", price_per_share: "", total_proceeds: "", currency: "EUR", fees: "", notes: "",
  });
  const [dividendForm, setDividendForm] = useState<DividendFormState>({
    security_id: "", account_id: "_unassigned", trade_date: "", gross_amount: "", currency: "EUR",
    wht_source_country: "", wht_source_rate: "", wht_source_amount: "",
    wht_dest_country: "", wht_dest_amount: "", notes: "",
  });
  const [transferForm, setTransferForm] = useState<TransferFormState>({
    security_id: "", source_account_id: "_unassigned", dest_account_id: "_unassigned",
    trade_date: "", quantity: "", carried_cost_basis: "", transfer_fees: "", notes: "",
  });

  const loadResources = useCallback(async () => {
    try {
      const [acctResp, secResp] = await Promise.allSettled([listAccounts(), listSecurities()]);
      if (acctResp.status === "fulfilled") setAccounts(acctResp.value.accounts);
      if (secResp.status === "fulfilled") setSecurities(secResp.value.securities);
    } finally {
      setLoadingResources(false);
    }
  }, []);

  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { loadResources(); }, [loadResources]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) { if (e.key === "Escape") onClose(); }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);

    try {
      const currency = txnType === "BUY" ? (buyForm.currency || "EUR")
        : txnType === "SELL" ? (sellForm.currency || "EUR")
        : txnType === "DIVIDEND" ? (dividendForm.currency || "EUR")
        : "EUR";

      // Helper to build AmountInput — eur_amount = amount when currency is already EUR
      function makeGross(amount: string, curr: string, eurAmount?: string) {
        return {
          amount,
          currency: curr,
          eur_amount: eurAmount || (curr === "EUR" ? amount : "0"),
        };
      }

      function makeFeesInput(total: string, curr: string) {
        return {
          total,
          currency: curr,
          total_eur: curr === "EUR" ? total : "0",
        };
      }

      if (txnType === "BUY") {
        if (!buyForm.security_id || !buyForm.trade_date || !buyForm.quantity || !buyForm.total_cost) {
          setError("Security, date, quantity, and total cost are required.");
          return;
        }
        const req: ManualMovementRequest = {
          txn_type: "BUY",
          security_id: buyForm.security_id,
          account_id: buyForm.account_id || "_unassigned",
          trade_date: buyForm.trade_date,
          quantity: buyForm.quantity,
          gross: makeGross(buyForm.total_cost, currency),
          fees: buyForm.fees ? makeFeesInput(buyForm.fees, currency) : undefined,
          notes: buyForm.notes || undefined,
        };
        await createMovement(req);

      } else if (txnType === "SELL") {
        if (!sellForm.security_id || !sellForm.trade_date || !sellForm.total_proceeds) {
          setError("Security, date, and total proceeds are required.");
          return;
        }
        if (sellForm.sales_type === "ACCIONES" && !sellForm.quantity) {
          setError("Quantity is required for ACCIONES sales.");
          return;
        }
        const req: ManualMovementRequest = {
          txn_type: "SELL",
          security_id: sellForm.security_id,
          account_id: sellForm.account_id || "_unassigned",
          trade_date: sellForm.trade_date,
          quantity: sellForm.quantity || undefined,
          sales_type: sellForm.sales_type,
          gross: makeGross(sellForm.total_proceeds, currency),
          fees: sellForm.fees ? makeFeesInput(sellForm.fees, currency) : undefined,
          notes: sellForm.notes || undefined,
        };
        await createMovement(req);

      } else if (txnType === "DIVIDEND") {
        if (!dividendForm.security_id || !dividendForm.trade_date || !dividendForm.gross_amount) {
          setError("Security, date, and gross amount are required.");
          return;
        }
        const req: ManualMovementRequest = {
          txn_type: "DIVIDEND",
          security_id: dividendForm.security_id,
          account_id: dividendForm.account_id || "_unassigned",
          trade_date: dividendForm.trade_date,
          gross: makeGross(dividendForm.gross_amount, currency),
          withholding: (dividendForm.wht_source_country || dividendForm.wht_source_amount)
            ? {
                source: {
                  country: dividendForm.wht_source_country || undefined,
                  rate_pct: dividendForm.wht_source_rate || undefined,
                  amount_eur: dividendForm.wht_source_amount || undefined,
                },
              }
            : undefined,
          notes: dividendForm.notes || undefined,
        };
        await createMovement(req);

      } else {
        // TRANSFER — uses POST /api/portfolio/transfers, not /movements
        if (!transferForm.security_id || !transferForm.trade_date || !transferForm.quantity) {
          setError("Security, date, and quantity are required.");
          return;
        }
        if (transferForm.source_account_id === transferForm.dest_account_id) {
          setError("Source and destination accounts cannot be the same.");
          return;
        }
        const req: TransferRequest = {
          security_id: transferForm.security_id,
          trade_date: transferForm.trade_date,
          quantity: transferForm.quantity,
          source_account_id: transferForm.source_account_id,
          dest_account_id: transferForm.dest_account_id,
          cost_basis_override_eur: transferForm.carried_cost_basis || null,
          transfer_fee: transferForm.transfer_fees
            ? makeFeesInput(transferForm.transfer_fees, "EUR")
            : undefined,
          notes: transferForm.notes || undefined,
        };
        await createTransfer(req);
      }

      setSuccess(true);
    } catch (err) {
      const e = err as { status?: number; data?: { detail?: string; error?: string } };
      if (e.status === 409 && e.data?.error === "insufficient_shares") {
        setError(
          e.data?.detail ??
            "Insufficient shares in source account. The source account does not hold enough shares for this transfer.",
        );
      } else {
        setError(e.data?.detail ?? (err instanceof Error ? err.message : "Failed to create movement"));
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-[200] flex items-start justify-center overflow-auto bg-black/60 p-4"
      onClick={onClose}
    >
      <div
        className="mt-10 mb-10 w-full max-w-[640px] rounded-[var(--radius)] border border-border bg-bg-card"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Add movement"
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <h3 className="text-base font-semibold text-text">Add Movement</h3>
          <button type="button" onClick={onClose} aria-label="Close" className="text-text-muted hover:text-text">
            <X size={18} />
          </button>
        </div>

        <div className="px-5 py-4 space-y-5">
          {/* Type selector */}
          <div>
            <div className={labelCls}>Movement type *</div>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {TXN_TYPES.map((t) => (
                <button
                  key={t.value}
                  type="button"
                  onClick={() => setTxnType(t.value)}
                  className={`rounded-[var(--radius)] border px-3 py-2 text-left text-sm transition-colors ${
                    txnType === t.value
                      ? "border-accent-blue/50 bg-accent-blue/10 text-accent-blue"
                      : "border-border text-text-muted hover:bg-bg-hover hover:text-text"
                  }`}
                >
                  <div className="font-medium">{t.label}</div>
                  <div className="text-xs opacity-70 mt-0.5 hidden sm:block">{t.description}</div>
                </button>
              ))}
            </div>
          </div>

          {/* Success state */}
          {success ? (
            <div className="space-y-3">
              <div className="rounded-[var(--radius)] border border-accent-green/30 bg-accent-green/5 px-4 py-3 text-sm">
                <p className="font-medium text-accent-green">✓ Movement recorded</p>
              </div>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={onCreated}
                  className="rounded-[var(--radius)] bg-accent-green/15 px-4 py-1.5 text-sm text-accent-green hover:bg-accent-green/25"
                >
                  Done
                </button>
                <button
                  type="button"
                  onClick={() => { setSuccess(false); setError(null); }}
                  className="rounded-[var(--radius)] border border-border px-4 py-1.5 text-sm text-text-muted hover:bg-bg-hover"
                >
                  Add another
                </button>
              </div>
            </div>
          ) : loadingResources ? (
            <div className="text-sm text-text-muted">Loading securities and accounts…</div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-4">
              {txnType === "BUY" && (
                <BuyForm form={buyForm} onChange={(f) => setBuyForm((s) => ({ ...s, ...f }))} accounts={accounts} securities={securities} />
              )}
              {txnType === "SELL" && (
                <SellForm form={sellForm} onChange={(f) => setSellForm((s) => ({ ...s, ...f }))} accounts={accounts} securities={securities} />
              )}
              {txnType === "DIVIDEND" && (
                <DividendForm form={dividendForm} onChange={(f) => setDividendForm((s) => ({ ...s, ...f }))} accounts={accounts} securities={securities} />
              )}
              {txnType === "TRANSFER" && (
                <TransferForm form={transferForm} onChange={(f) => setTransferForm((s) => ({ ...s, ...f }))} accounts={accounts} securities={securities} />
              )}

              {error && (
                <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-2 text-sm text-accent-red">
                  {error}
                </div>
              )}

              <div className="flex gap-2 border-t border-border/40 pt-4">
                <button
                  type="submit"
                  disabled={saving}
                  className="rounded-[var(--radius)] bg-accent-blue/15 px-4 py-1.5 text-sm text-accent-blue hover:bg-accent-blue/25 disabled:opacity-50"
                >
                  {saving ? "Saving…" : "Add movement"}
                </button>
                <button
                  type="button"
                  onClick={onClose}
                  disabled={saving}
                  className="rounded-[var(--radius)] border border-border px-4 py-1.5 text-sm text-text-muted hover:bg-bg-hover"
                >
                  Cancel
                </button>
              </div>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
