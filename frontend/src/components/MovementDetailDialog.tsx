"use client";

import { useEffect, useState, useCallback } from "react";
import { X, History } from "lucide-react";
import type { LedgerMovement, WarningType } from "@/types/portfolio";
import MovementCorrectionDialog from "./MovementCorrectionDialog";
import ReassignmentDialog from "./ReassignmentDialog";

const TXN_BADGE: Record<string, string> = {
  BUY: "bg-accent-green/15 text-accent-green",
  SELL: "bg-accent-red/15 text-accent-red",
  DIVIDEND: "bg-accent-blue/15 text-accent-blue",
  TRANSFER_OUT: "bg-accent-orange/15 text-accent-orange",
  TRANSFER_IN: "bg-accent-orange/15 text-accent-orange",
};

const WARNING_SHORT: Record<WarningType, string> = {
  NEGATIVE_INVENTORY: "Negative inventory",
  ZERO_COST_ACQUISITION: "Incomplete cost basis",
  RIGHTS_AMOUNT: "Rights amount pending",
  PROBABLE_DUPLICATE: "Probable duplicate",
  DERECHOS_WITH_QUANTITY: "Rights sale with quantity",
  ACCIONES_ZERO_QUANTITY: "Share sale, zero quantity",
  INVALID_SALES_TYPE: "Invalid sale type",
};

function Field({ label, value, mono = false }: { label: string; value: string | null | undefined; mono?: boolean }) {
  return (
    <div>
      <div className="text-xs text-text-muted mb-0.5">{label}</div>
      <div className={`text-sm ${mono ? "font-mono" : ""} ${value == null ? "text-text-muted italic" : "text-text"}`}>
        {value ?? "—"}
      </div>
    </div>
  );
}

function formatEurAmount(amount: string | null | undefined, currency?: string): string {
  if (!amount) return "—";
  const n = Number(amount);
  if (isNaN(n)) return amount;
  const eur = `€${n.toLocaleString("es-ES", { minimumFractionDigits: 2 })}`;
  if (currency && currency !== "EUR") {
    return `${eur} (${currency})`;
  }
  return eur;
}

function accountDisplay(accountId: string): string {
  if (!accountId || accountId === "_unassigned") return "Sin asignar";
  return accountId;
}

export interface MovementDetailDialogProps {
  movement: LedgerMovement;
  onClose: () => void;
  onRefresh: () => void;
}

export default function MovementDetailDialog({ movement: m, onClose, onRefresh }: MovementDetailDialogProps) {
  const [showCorrect, setShowCorrect] = useState(false);
  const [showReassign, setShowReassign] = useState(false);

  const handleClose = useCallback(() => onClose(), [onClose]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) { if (e.key === "Escape") handleClose(); }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [handleClose]);

  const importSourceLabel = m.import_source === "csv_import" ? "CSV Import" : "Manual";

  if (showCorrect) {
    return (
      <MovementCorrectionDialog
        movement={m}
        onClose={() => setShowCorrect(false)}
        onCorrected={() => { setShowCorrect(false); onRefresh(); onClose(); }}
      />
    );
  }

  if (showReassign) {
    return (
      <ReassignmentDialog
        mode="individual"
        movementId={m.id}
        currentAccountId={m.account_id}
        onClose={() => setShowReassign(false)}
        onReassigned={() => { setShowReassign(false); onRefresh(); onClose(); }}
      />
    );
  }

  return (
    <div
      className="fixed inset-0 z-[200] flex items-start justify-center overflow-auto bg-black/60 p-4"
      onClick={handleClose}
    >
      <div
        className="mt-12 mb-12 w-full max-w-[620px] rounded-[var(--radius)] border border-border bg-bg-card"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Movement detail"
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <div className="flex items-center gap-2 flex-wrap">
            <span
              className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                TXN_BADGE[m.txn_type] ?? "bg-bg-hover text-text-muted"
              }`}
            >
              {m.txn_type}
            </span>
            {m.correction_status && m.correction_status !== "ACTIVE" && (
              <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                m.correction_status === "SUPERSEDED"
                  ? "bg-accent-orange/15 text-accent-orange"
                  : "bg-accent-red/15 text-accent-red"
              }`}>
                {m.correction_status}
              </span>
            )}
            <h3 className="text-base font-semibold text-text">
              {m.ticker} — {m.company_name}
            </h3>
          </div>
          <button
            type="button"
            onClick={handleClose}
            aria-label="Close"
            className="rounded-[var(--radius)] p-1 text-text-muted hover:bg-bg-hover hover:text-text transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="px-5 py-4 space-y-5">
          {/* Core fields */}
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
            <Field label="Date" value={m.trade_date} mono />
            <Field label="Security ID" value={m.security_id} mono />
            <Field label="Account" value={accountDisplay(m.account_id)} />
            {m.quantity != null && (
              <Field
                label="Quantity"
                value={Number(m.quantity).toLocaleString("es-ES", { maximumFractionDigits: 6 })}
                mono
              />
            )}
            {m.txn_type === "SELL" && (
              <Field
                label="Sale type"
                value={m.sales_type === "DERECHOS" ? "Derechos (rights)" : m.sales_type === "ACCIONES" ? "Acciones (shares)" : null}
              />
            )}
          </div>

          {/* Amounts */}
          <div>
            <div className="text-xs font-semibold uppercase tracking-wide text-text-muted mb-2">Amounts</div>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 rounded-[var(--radius)] border border-border bg-bg-card/50 p-3">
              <Field label="Gross" value={formatEurAmount(m.gross.eur_amount, m.gross.currency)} mono />
              <Field label="Fees" value={formatEurAmount(m.fees.total_eur, m.fees.currency)} mono />
              <Field label="Net" value={formatEurAmount(m.net.eur_amount, m.net.currency)} mono />
              {m.withholding.source && (
                <Field
                  label={`WHT Source (${m.withholding.source.country ?? ""})`}
                  value={formatEurAmount(m.withholding.source.amount_eur)}
                  mono
                />
              )}
              {m.withholding.destination && (
                <Field
                  label={`WHT Dest`}
                  value={formatEurAmount(m.withholding.destination.amount_eur)}
                  mono
                />
              )}
            </div>
          </div>

          {/* FX */}
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
            <Field label="FX Rate" value={m.fx?.rate ?? null} mono />
            <Field label="FX Source" value={m.fx?.rate_source ?? null} />
            <Field label="Import source" value={importSourceLabel} />
          </div>

          {/* Cost basis status */}
          {m.cost_basis_status === "INCOMPLETE" && (
            <div className="rounded-[var(--radius)] border border-accent-orange/30 bg-accent-orange/5 px-4 py-2 text-sm text-text-muted">
              <span className="text-accent-orange mr-1">⚠</span>
              Cost basis incomplete — likely a zero-cost corporate-action acquisition.
            </div>
          )}

          {/* Derechos note */}
          {m.txn_type === "SELL" && m.sales_type === "DERECHOS" && (
            <div className="rounded-[var(--radius)] border border-accent-blue/20 bg-accent-blue/5 px-4 py-2 text-xs text-text-muted">
              ℹ Rights (derechos) sale: proceeds are recorded but <strong className="text-text">share quantity is not reduced</strong> — rights entitlements are separate from ordinary share ownership.
            </div>
          )}

          {/* Warnings */}
          {m.warnings && m.warnings.length > 0 && (
            <div className="rounded-[var(--radius)] border border-accent-orange/30 bg-accent-orange/5 px-4 py-2 space-y-1">
              {m.warnings.map((w, i) => (
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
          )}

          {/* Transfer details */}
          {(m.txn_type === "TRANSFER_OUT" || m.txn_type === "TRANSFER_IN") && (
            <div>
              <div className="text-xs font-semibold uppercase tracking-wide text-text-muted mb-2">Transfer</div>
              <div className="grid grid-cols-2 gap-4 rounded-[var(--radius)] border border-border bg-bg-card/50 p-3">
                <Field label="Direction" value={m.txn_type === "TRANSFER_OUT" ? "Out → destination" : "In ← source"} />
                {m.transfer_source_account_id && (
                  <Field label="From account" value={accountDisplay(m.transfer_source_account_id)} />
                )}
                {m.transfer_dest_account_id && (
                  <Field label="To account" value={accountDisplay(m.transfer_dest_account_id)} />
                )}
                {m.transfer_cost_basis_eur && (
                  <Field
                    label={`Cost basis EUR${m.transfer_cost_basis_overridden ? " (overridden)" : " (derived)"}`}
                    value={formatEurAmount(m.transfer_cost_basis_eur)}
                    mono
                  />
                )}
                {m.transfer_fee && m.transfer_fee.total_eur && (
                  <Field label="Transfer fee" value={formatEurAmount(m.transfer_fee.total_eur, m.transfer_fee.currency)} mono />
                )}
                {m.transfer_group_id && (
                  <Field label="Group ID" value={m.transfer_group_id} mono />
                )}
              </div>
            </div>
          )}

          {/* Correction audit chain */}
          {(m.corrects_movement_id || m.superseded_by || m.correction_note) && (
            <div className="rounded-[var(--radius)] border border-accent-orange/20 bg-accent-orange/5 px-4 py-3 text-xs space-y-1">
              <div className="font-medium text-text-muted uppercase tracking-wide mb-1">Correction audit</div>
              {m.correction_note && (
                <div><span className="text-text-muted">Note: </span><span className="text-text">{m.correction_note}</span></div>
              )}
              {m.corrects_movement_id && (
                <div><span className="text-text-muted">Corrects: </span><span className="font-mono text-text-muted">{m.corrects_movement_id}</span></div>
              )}
              {m.superseded_by && (
                <div><span className="text-text-muted">Superseded by: </span><span className="font-mono text-text-muted">{m.superseded_by}</span></div>
              )}
            </div>
          )}

          {/* Movement ID */}
          <div className="text-xs text-text-muted font-mono border-t border-border/40 pt-2">
            ID: {m.id}
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center justify-between gap-2 border-t border-border px-5 py-3">
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => setShowCorrect(true)}
              className="inline-flex items-center gap-1.5 rounded-[var(--radius)] border border-border px-3 py-1.5 text-xs text-text-muted hover:bg-bg-hover hover:text-text transition-colors"
            >
              <History size={13} />
              Correct movement
            </button>
            <button
              type="button"
              onClick={() => setShowReassign(true)}
              className="inline-flex items-center gap-1.5 rounded-[var(--radius)] border border-border px-3 py-1.5 text-xs text-text-muted hover:bg-bg-hover hover:text-text transition-colors"
            >
              Reassign account
            </button>
          </div>
          <button
            type="button"
            onClick={handleClose}
            className="rounded-[var(--radius)] border border-border px-3 py-1.5 text-xs text-text-muted hover:bg-bg-hover"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
