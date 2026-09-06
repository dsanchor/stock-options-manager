"use client";

import { useState } from "react";
import { createSecurity } from "@/lib/portfolio-api";
import type { CreateSecurityRequest, SecurityMaster } from "@/types/portfolio";

interface Props {
  onCreated: (security: SecurityMaster) => void;
  onCancel: () => void;
  prefillName?: string;
}

/**
 * Inline form for creating a new security_master entry.
 * Asks: ticker, MIC, listing currency, display name. Optional: ISIN/provider identifiers.
 * Does NOT enable agents/watchlists or imply ownership.
 */
export default function SecurityCreateForm({ onCreated, onCancel, prefillName }: Props) {
  const [ticker, setTicker] = useState("");
  const [mic, setMic] = useState("");
  const [currency, setCurrency] = useState("");
  const [name, setName] = useState(prefillName ?? "");
  const [isin, setIsin] = useState("");
  const [cusip, setCusip] = useState("");
  const [sedol, setSedol] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showOptional, setShowOptional] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);

    const payload: CreateSecurityRequest = {
      ticker: ticker.trim().toUpperCase(),
      exchange_mic: mic.trim().toUpperCase(),
      listing_currency: currency.trim().toUpperCase(),
      company_name: name.trim(),
      asset_class: "Equity",
    };
    if (isin.trim()) payload.isin = isin.trim().toUpperCase();
    if (cusip.trim()) payload.cusip = cusip.trim().toUpperCase();
    if (sedol.trim()) payload.sedol = sedol.trim().toUpperCase();

    try {
      const created = await createSecurity(payload);
      onCreated(created);
    } catch (err) {
      const e = err as { status?: number; data?: { error?: string; detail?: string } };
      if (e.status === 409) {
        setError(
          `A security with this ISIN or identifier already exists: ${e.data?.detail ?? "collision"}`,
        );
      } else {
        setError(e.data?.detail ?? (err instanceof Error ? err.message : "Creation failed"));
      }
    } finally {
      setLoading(false);
    }
  }

  const inputCls =
    "w-full rounded-[var(--radius)] border border-border bg-bg-input px-3 py-2 text-sm text-text placeholder:text-text-muted focus:border-accent-blue focus:outline-none";
  const labelCls = "block text-xs font-medium text-text-muted mb-1";

  return (
    <form
      onSubmit={handleSubmit}
      className="mt-3 rounded-[var(--radius)] border border-border/60 bg-bg-card-2 p-4 space-y-4"
    >
      <div className="text-sm font-medium text-text">Create new security</div>

      {error && (
        <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-3 py-2 text-sm text-accent-red">
          {error}
        </div>
      )}

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className={labelCls}>Ticker *</label>
          <input
            required
            value={ticker}
            onChange={(e) => setTicker(e.target.value)}
            placeholder="AAPL"
            className={inputCls}
          />
        </div>
        <div>
          <label className={labelCls}>Exchange MIC *</label>
          <input
            required
            value={mic}
            onChange={(e) => setMic(e.target.value)}
            placeholder="XNYS"
            className={inputCls}
          />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className={labelCls}>Display name *</label>
          <input
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Apple Inc."
            className={inputCls}
          />
        </div>
        <div>
          <label className={labelCls}>Listing currency *</label>
          <input
            required
            value={currency}
            onChange={(e) => setCurrency(e.target.value)}
            placeholder="USD"
            maxLength={3}
            className={inputCls}
          />
        </div>
      </div>

      <button
        type="button"
        onClick={() => setShowOptional((v) => !v)}
        className="text-xs text-accent-blue hover:underline"
      >
        {showOptional ? "▲ Hide identifiers" : "▶ Add ISIN / CUSIP / SEDOL (optional)"}
      </button>

      {showOptional && (
        <div className="grid grid-cols-3 gap-3">
          <div>
            <label className={labelCls}>ISIN</label>
            <input
              value={isin}
              onChange={(e) => setIsin(e.target.value)}
              placeholder="US0378331005"
              className={inputCls}
            />
          </div>
          <div>
            <label className={labelCls}>CUSIP</label>
            <input
              value={cusip}
              onChange={(e) => setCusip(e.target.value)}
              placeholder="037833100"
              className={inputCls}
            />
          </div>
          <div>
            <label className={labelCls}>SEDOL</label>
            <input
              value={sedol}
              onChange={(e) => setSedol(e.target.value)}
              placeholder="2046251"
              className={inputCls}
            />
          </div>
        </div>
      )}

      <div className="flex gap-2 justify-end">
        <button
          type="button"
          onClick={onCancel}
          disabled={loading}
          className="rounded-[var(--radius)] border border-border px-3 py-1.5 text-sm text-text-muted hover:bg-bg-hover disabled:opacity-50"
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={loading}
          className="rounded-[var(--radius)] bg-[image:var(--grad-blue)] px-4 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
        >
          {loading ? "Creating…" : "Create security"}
        </button>
      </div>
    </form>
  );
}
