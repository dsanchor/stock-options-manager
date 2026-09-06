"use client";

import { useState, useCallback, useEffect } from "react";
import Link from "next/link";
import { FileUp, Plus } from "lucide-react";
import { getMovements, deleteMovement, listAccounts } from "@/lib/portfolio-api";
import type { MovementsResponse, LedgerMovement, TxnType, WarningType } from "@/types/portfolio";
import type { BrokerAccount } from "@/types/portfolio";
import type { MovementsFilter } from "@/lib/portfolio-api";
import MovementDetailDialog from "./MovementDetailDialog";
import AddMovementDialog from "./AddMovementDialog";

const TXN_BADGE: Record<TxnType, string> = {
  BUY: "bg-accent-green/15 text-accent-green",
  SELL: "bg-accent-red/15 text-accent-red",
  DIVIDEND: "bg-accent-blue/15 text-accent-blue",
  TRANSFER_OUT: "bg-accent-orange/15 text-accent-orange",
  TRANSFER_IN: "bg-accent-orange/15 text-accent-orange",
};

const WARNING_SHORT: Record<WarningType, string> = {
  NEGATIVE_INVENTORY: "Negative inventory",
  ZERO_COST_ACQUISITION: "Incomplete cost basis",
  RIGHTS_AMOUNT: "Rights amount",
  PROBABLE_DUPLICATE: "Probable duplicate",
  DERECHOS_WITH_QUANTITY: "Rights sale with quantity",
  ACCIONES_ZERO_QUANTITY: "Share sale, zero quantity",
  INVALID_SALES_TYPE: "Invalid sale type",
};

function accountDisplay(id: string): string {
  return !id || id === "_unassigned" ? "Sin asignar" : id;
}

const PAGE_SIZE = 50;

function Skeleton() {
  return (
    <div className="space-y-2">
      {[...Array(6)].map((_, i) => (
        <div key={i} className="skeleton h-10 rounded-[var(--radius)]" />
      ))}
    </div>
  );
}

/** Client-side movements table with account filter, pagination, detail view, and manual entry. */
export default function PortfolioMovementsTable() {
  const [data, setData] = useState<MovementsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [accounts, setAccounts] = useState<BrokerAccount[]>([]);
  const [selectedMovement, setSelectedMovement] = useState<LedgerMovement | null>(null);
  const [showAddMovement, setShowAddMovement] = useState(false);

  // Filters
  const [accountFilter, setAccountFilter] = useState("");
  const [txnType, setTxnType] = useState("");
  const [securityId, setSecurityId] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  const buildFilter = useCallback((): MovementsFilter => ({
    account_id: accountFilter || undefined,
    txn_type: txnType || undefined,
    security_id: securityId.trim() || undefined,
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
  }), [accountFilter, txnType, securityId, dateFrom, dateTo]);

  const load = useCallback(async (off: number, filter: MovementsFilter) => {
    setLoading(true);
    setError(null);
    try {
      const d = await getMovements({ ...filter, limit: PAGE_SIZE, offset: off });
      setData(d);
    } catch (err) {
      const e = err as { status?: number; data?: { error?: string; detail?: string } };
      if (e.status === 503) {
        setError("Portfolio storage is not yet configured.");
      } else {
        setError(e.data?.detail ?? (err instanceof Error ? err.message : "Failed to load movements"));
      }
    } finally {
      setLoading(false);
    }
  }, []);

  const loadAccounts = useCallback(async () => {
    try {
      const resp = await listAccounts();
      setAccounts(resp.accounts);
    } catch {
      setAccounts([]);
    }
  }, []);

  // eslint-disable-next-line react-hooks/set-state-in-effect, react-hooks/exhaustive-deps
  useEffect(() => { load(0, {}); loadAccounts(); }, []);

  function applyFilter() {
    setOffset(0);
    load(0, buildFilter());
  }

  function resetFilter() {
    setAccountFilter("");
    setTxnType("");
    setSecurityId("");
    setDateFrom("");
    setDateTo("");
    setOffset(0);
    load(0, {});
  }

  async function handleDelete(id: string, accountId?: string) {
    setDeleteError(null);
    try {
      await deleteMovement(id, accountId);
      load(offset, buildFilter());
    } catch (err) {
      const e = err as { data?: { detail?: string; error?: string } };
      setDeleteError(e.data?.detail ?? (err instanceof Error ? err.message : "Delete failed"));
    }
  }

  const inputCls =
    "rounded-[var(--radius)] border border-border bg-bg-input px-3 py-1.5 text-sm text-text placeholder:text-text-muted focus:border-accent-blue focus:outline-none";

  return (
    <div className="space-y-5">
      {/* Filter bar */}
      <div className="flex flex-wrap gap-3 rounded-[var(--radius)] border border-border bg-bg-card px-4 py-3">
        {/* Account filter */}
        <select
          value={accountFilter}
          onChange={(e) => setAccountFilter(e.target.value)}
          className={`${inputCls} w-40`}
          aria-label="Filter by account"
        >
          <option value="">All accounts</option>
          <option value="_unassigned">Sin asignar</option>
          {accounts.map((a) => (
            <option key={a.account_id} value={a.account_id}>{a.name}</option>
          ))}
        </select>

        <select
          value={txnType}
          onChange={(e) => setTxnType(e.target.value)}
          className={`${inputCls} w-32`}
        >
          <option value="">All types</option>
          <option value="BUY">BUY</option>
          <option value="SELL">SELL</option>
          <option value="DIVIDEND">DIVIDEND</option>
          <option value="TRANSFER_OUT">TRANSFER OUT</option>
          <option value="TRANSFER_IN">TRANSFER IN</option>
        </select>
        <input
          value={securityId}
          onChange={(e) => setSecurityId(e.target.value)}
          placeholder="Security ID"
          className={`${inputCls} w-36`}
        />
        <input
          type="date"
          value={dateFrom}
          onChange={(e) => setDateFrom(e.target.value)}
          className={`${inputCls} w-36`}
          title="From date"
        />
        <input
          type="date"
          value={dateTo}
          onChange={(e) => setDateTo(e.target.value)}
          className={`${inputCls} w-36`}
          title="To date"
        />
        <button
          type="button"
          onClick={applyFilter}
          className="rounded-[var(--radius)] bg-accent-blue/15 px-3 py-1.5 text-sm text-accent-blue hover:bg-accent-blue/25"
        >
          Apply
        </button>
        <button
          type="button"
          onClick={resetFilter}
          className="rounded-[var(--radius)] border border-border px-3 py-1.5 text-sm text-text-muted hover:bg-bg-hover"
        >
          Reset
        </button>
        <div className="ml-auto flex items-center gap-2">
          <button
            type="button"
            onClick={() => setShowAddMovement(true)}
            className="inline-flex items-center gap-1.5 rounded-[var(--radius)] bg-accent-blue/15 px-3 py-1.5 text-sm text-accent-blue hover:bg-accent-blue/25"
          >
            <Plus size={14} className="shrink-0" />
            Add movement
          </button>
          <Link
            href="/portfolio/import"
            className="inline-flex items-center gap-1.5 rounded-[var(--radius)] border border-border px-3 py-1.5 text-sm text-text-muted hover:bg-bg-hover"
          >
            <FileUp size={14} className="shrink-0" />
            Bulk import
          </Link>
        </div>
      </div>

      {deleteError && (
        <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-2 text-sm text-accent-red">
          {deleteError}
        </div>
      )}

      {error && (
        <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm">
          ⚠️ {error}
        </div>
      )}

      {loading ? (
        <Skeleton />
      ) : !data || data.movements.length === 0 ? (
        <div className="rounded-[var(--radius-card)] border border-border bg-bg-card p-10 text-center space-y-3">
          <div className="text-3xl">📋</div>
          <div className="text-sm font-medium text-text">No movements found</div>
          <div className="text-xs text-text-muted">
            {offset > 0 || txnType || securityId || accountFilter
              ? "Try adjusting the filters."
              : "Import a CSV or add a movement to start your ledger."}
          </div>
          {!txnType && !securityId && !accountFilter && (
            <div className="flex items-center justify-center gap-3">
              <button
                type="button"
                onClick={() => setShowAddMovement(true)}
                className="inline-flex items-center gap-1.5 rounded-[var(--radius)] bg-[image:var(--grad-blue)] px-4 py-2 text-sm font-medium text-white hover:opacity-90"
              >
                <Plus size={14} /> Add movement
              </button>
              <Link
                href="/portfolio/import"
                className="inline-flex items-center rounded-[var(--radius)] border border-border px-4 py-2 text-sm font-medium text-text-muted hover:bg-bg-hover"
              >
                Import CSV
              </Link>
            </div>
          )}
        </div>
      ) : (
        <>
          {/* Table */}
          <div className="overflow-x-auto rounded-[var(--radius)] border border-border">
            <table className="w-full table-modern text-sm">
              <thead>
                <tr className="border-b border-border bg-bg-card/80">
                  {["Type", "Security", "Date", "Qty", "Gross (€)", "Net (€)", "Account", ""].map(
                    (h, i) => (
                      <th
                        key={i}
                        className={`px-4 py-2.5 text-xs font-semibold uppercase tracking-wide text-text-muted ${
                          i >= 2 && i <= 5 ? "text-right" : "text-left"
                        }`}
                      >
                        {h}
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody className="divide-y divide-border/40">
                {data.movements.map((m) => (
                  <MovementRow
                    key={m.id}
                    movement={m}
                    onDelete={handleDelete}
                    onSelect={() => setSelectedMovement(m)}
                  />
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div className="flex items-center justify-between text-sm text-text-muted">
            <span>
              Showing {offset + 1}–{Math.min(offset + PAGE_SIZE, data.total_count)} of{" "}
              {data.total_count}
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => {
                  const next = Math.max(0, offset - PAGE_SIZE);
                  setOffset(next);
                  load(next, buildFilter());
                }}
                disabled={offset === 0}
                className="rounded-[var(--radius)] border border-border px-3 py-1.5 hover:bg-bg-hover disabled:opacity-40"
              >
                ← Prev
              </button>
              <button
                type="button"
                onClick={() => {
                  const next = offset + PAGE_SIZE;
                  setOffset(next);
                  load(next, buildFilter());
                }}
                disabled={offset + PAGE_SIZE >= data.total_count}
                className="rounded-[var(--radius)] border border-border px-3 py-1.5 hover:bg-bg-hover disabled:opacity-40"
              >
                Next →
              </button>
            </div>
          </div>
        </>
      )}

      {/* Detail dialog */}
      {selectedMovement && (
        <MovementDetailDialog
          movement={selectedMovement}
          onClose={() => setSelectedMovement(null)}
          onRefresh={() => { setSelectedMovement(null); load(offset, buildFilter()); }}
        />
      )}

      {/* Add movement dialog */}
      {showAddMovement && (
        <AddMovementDialog
          onClose={() => setShowAddMovement(false)}
          onCreated={() => { setShowAddMovement(false); load(0, buildFilter()); }}
        />
      )}
    </div>
  );
}

function MovementRow({
  movement: m,
  onDelete,
  onSelect,
}: {
  movement: LedgerMovement;
  onDelete: (id: string, accountId?: string) => void;
  onSelect: () => void;
}) {
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [showWarnings, setShowWarnings] = useState(false);
  const hasWarnings = m.warnings && m.warnings.length > 0;
  const hasIncomplete = m.cost_basis_status === "INCOMPLETE";
  const showWarningIcon = hasWarnings || hasIncomplete;

  return (
    <>
      <tr className="cursor-pointer hover:bg-bg-hover/30 transition-colors" onClick={onSelect}>
        <td className="px-4 py-2" onClick={(e) => e.stopPropagation()}>
          <span
            className={`rounded-full px-2 py-0.5 text-xs font-medium ${
              TXN_BADGE[m.txn_type] ?? "bg-bg-hover text-text-muted"
            }`}
          >
            {m.txn_type}
          </span>
          {m.txn_type === "SELL" && m.is_rights_sale && (
            <span className="ml-1 rounded-full px-1.5 py-0.5 text-xs bg-accent-orange/15 text-accent-orange">
              Derechos
            </span>
          )}
        </td>
        <td className="px-4 py-2">
          <div className="font-mono font-semibold text-text">{m.ticker}</div>
          <div className="text-xs text-text-muted truncate max-w-[160px]">{m.company_name}</div>
        </td>
        <td className="px-4 py-2 text-right text-text-muted">{m.trade_date}</td>
        <td className="px-4 py-2 text-right font-mono text-text">
          {m.quantity != null
            ? Number(m.quantity).toLocaleString("es-ES", { maximumFractionDigits: 6 })
            : "—"}
        </td>
        <td className="px-4 py-2 text-right font-mono text-text">
          €{Number(m.gross.eur_amount).toLocaleString("es-ES", { minimumFractionDigits: 2 })}
        </td>
        <td className="px-4 py-2 text-right font-mono text-text">
          €{Number(m.net.eur_amount).toLocaleString("es-ES", { minimumFractionDigits: 2 })}
        </td>
        <td className="px-4 py-2 text-text-muted text-sm">{accountDisplay(m.account_id)}</td>
        <td className="px-3 py-2" onClick={(e) => e.stopPropagation()}>
          <div className="flex items-center gap-2 justify-end">
            {showWarningIcon && (
              <button
                type="button"
                onClick={() => setShowWarnings((v) => !v)}
                title="View warnings"
                className="text-accent-orange hover:opacity-70 transition-opacity"
              >
                ⚠
              </button>
            )}
            {confirmDelete ? (
              <span className="flex gap-1">
                <button
                  type="button"
                  onClick={() => onDelete(m.id, m.account_id)}
                  className="text-xs text-accent-red hover:underline"
                >
                  Confirm
                </button>
                <button
                  type="button"
                  onClick={() => setConfirmDelete(false)}
                  className="text-xs text-text-muted hover:underline"
                >
                  Cancel
                </button>
              </span>
            ) : (
              <button
                type="button"
                onClick={() => setConfirmDelete(true)}
                className="text-xs text-text-muted hover:text-accent-red transition-colors"
                title="Soft-delete movement"
              >
                ×
              </button>
            )}
          </div>
        </td>
      </tr>
      {showWarnings && showWarningIcon && (
        <tr>
          <td colSpan={8} className="px-4 pb-3 pt-0">
            <div className="rounded-[var(--radius)] border border-accent-orange/30 bg-accent-orange/5 px-4 py-2 space-y-1">
              {hasIncomplete && !hasWarnings && (
                <div className="text-xs text-text-muted">
                  <span className="font-medium text-text mr-1">Incomplete cost basis:</span>
                  Zero-cost acquisition — cost basis not yet assigned.
                </div>
              )}
              {m.warnings?.map((w, i) => (
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
