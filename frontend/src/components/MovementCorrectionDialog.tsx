"use client";

import { useEffect, useState } from "react";
import { X, History } from "lucide-react";
import { correctMovement } from "@/lib/portfolio-api";
import type { LedgerMovement } from "@/types/portfolio";

const inputCls =
  "w-full rounded-[var(--radius)] border border-border bg-bg-input px-3 py-2 text-sm text-text placeholder:text-text-muted focus:border-accent-blue focus:outline-none";
const labelCls = "mb-1 block text-xs font-medium text-text-muted";

export interface MovementCorrectionDialogProps {
  movement: LedgerMovement;
  onClose: () => void;
  onCorrected: () => void;
}

export default function MovementCorrectionDialog({
  movement: m,
  onClose,
  onCorrected,
}: MovementCorrectionDialogProps) {
  const [reason, setReason] = useState("");
  const [tradeDate, setTradeDate] = useState(m.trade_date ?? "");
  const [quantity, setQuantity] = useState(m.quantity ?? "");
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<{ originalId: string; correctedId: string } | null>(null);

  useEffect(() => {
    function onKey(e: KeyboardEvent) { if (e.key === "Escape") onClose(); }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!reason.trim()) {
      setError("A correction reason is required for the audit trail.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const hasChanges =
        (tradeDate && tradeDate !== m.trade_date) ||
        (quantity && quantity !== (m.quantity ?? "")) ||
        notes.trim().length > 0;

      if (!hasChanges) {
        setError("No fields changed. Edit at least one field to create a correction.");
        setSaving(false);
        return;
      }

      const result = await correctMovement(m.id, {
        account_id: m.account_id,
        correction_note: reason.trim(),
        trade_date: tradeDate !== m.trade_date ? tradeDate : undefined,
        quantity: quantity && quantity !== (m.quantity ?? "") ? quantity : undefined,
        notes: notes.trim() || undefined,
      });
      setSuccess({ originalId: result.original.id, correctedId: result.replacement.id });
    } catch (err) {
      const e = err as { data?: { detail?: string } };
      setError(e.data?.detail ?? (err instanceof Error ? err.message : "Correction failed"));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-[210] flex items-start justify-center overflow-auto bg-black/70 p-4"
      onClick={onClose}
    >
      <div
        className="mt-12 mb-12 w-full max-w-[580px] rounded-[var(--radius)] border border-border bg-bg-card"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Correct movement"
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <div className="flex items-center gap-2 text-accent-orange">
            <History size={16} />
            <h3 className="text-base font-semibold text-text">Correct Movement</h3>
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="text-text-muted hover:text-text">
            <X size={18} />
          </button>
        </div>

        <div className="px-5 py-4 space-y-4">
          {/* Audit notice */}
          <div className="rounded-[var(--radius)] border border-accent-orange/30 bg-accent-orange/5 px-4 py-3 text-sm text-text-muted">
            <p className="font-medium text-text mb-1">Auditable correction — original preserved</p>
            <p>
              Saving will create a <strong className="text-text">corrected replacement</strong> movement.
              The original is <strong className="text-text">preserved in history</strong> and marked as superseded.
              No data is deleted.
            </p>
          </div>

          {/* Original movement summary */}
          <div className="rounded-[var(--radius)] bg-bg-input/50 px-4 py-3 text-xs space-y-1">
            <div className="font-semibold text-text-muted uppercase tracking-wide mb-1">Original (will be preserved)</div>
            <div className="grid grid-cols-2 gap-1">
              <span className="text-text-muted">Type:</span><span className="font-mono text-text">{m.txn_type}</span>
              <span className="text-text-muted">Security:</span><span className="font-mono text-text">{m.ticker} ({m.security_id})</span>
              <span className="text-text-muted">Date:</span><span className="font-mono text-text">{m.trade_date}</span>
              {m.quantity != null && (
                <><span className="text-text-muted">Quantity:</span><span className="font-mono text-text">{m.quantity}</span></>
              )}
              <span className="text-text-muted">ID:</span><span className="font-mono text-text-muted truncate">{m.id}</span>
            </div>
          </div>

          {success ? (
            /* Success state */
            <div className="space-y-3">
              <div className="rounded-[var(--radius)] border border-accent-green/30 bg-accent-green/5 px-4 py-3 text-sm">
                <p className="font-medium text-accent-green mb-1">✓ Correction recorded</p>
                <div className="text-xs text-text-muted space-y-0.5">
                  <div>Original ID: <span className="font-mono">{success.originalId}</span></div>
                  <div>Corrected ID: <span className="font-mono">{success.correctedId}</span></div>
                </div>
              </div>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={onCorrected}
                  className="rounded-[var(--radius)] bg-accent-green/15 px-4 py-1.5 text-sm text-accent-green hover:bg-accent-green/25"
                >
                  Done
                </button>
              </div>
            </div>
          ) : (
            /* Correction form */
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className={`${labelCls} text-accent-red`}>Correction reason * (for audit trail)</label>
                <input
                  type="text"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="e.g. Wrong trade date entered during manual entry"
                  className={`${inputCls} border-accent-orange/50`}
                  required
                />
              </div>

              <div className="text-xs font-semibold uppercase tracking-wide text-text-muted">
                Corrected values (leave unchanged to keep original)
              </div>

              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div>
                  <label className={labelCls}>Trade date</label>
                  <input type="date" value={tradeDate} onChange={(e) => setTradeDate(e.target.value)} className={inputCls} />
                </div>
                {m.quantity != null && (
                  <div>
                    <label className={labelCls}>Quantity</label>
                    <input
                      type="number"
                      step="any"
                      value={quantity}
                      onChange={(e) => setQuantity(e.target.value)}
                      placeholder="Shares"
                      className={inputCls}
                    />
                  </div>
                )}
                <div>
                  <label className={labelCls}>Gross amount (EUR) — leave blank to keep original</label>
                  <input
                    type="text"
                    placeholder={m.gross.eur_amount ?? ""}
                    className={inputCls}
                    disabled
                    title="Amount corrections require submitting a new gross object; use import re-entry for amount-only corrections."
                  />
                </div>
              </div>

              <div>
                <label className={labelCls}>Correction notes</label>
                <textarea
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder="Additional context (optional)"
                  rows={2}
                  className={`${inputCls} resize-none`}
                />
              </div>

              {error && (
                <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-3 py-2 text-sm text-accent-red">
                  {error}
                </div>
              )}

              <div className="flex gap-2">
                <button
                  type="submit"
                  disabled={saving}
                  className="rounded-[var(--radius)] bg-accent-orange/15 px-4 py-1.5 text-sm text-accent-orange hover:bg-accent-orange/25 disabled:opacity-50"
                >
                  {saving ? "Saving…" : "Save correction"}
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
