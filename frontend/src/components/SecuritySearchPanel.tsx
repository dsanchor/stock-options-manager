"use client";

import { useEffect, useRef, useState } from "react";
import { listSecurities } from "@/lib/portfolio-api";
import { filterSecurities } from "@/lib/filterSecurities";
import type { SecurityMaster } from "@/types/portfolio";

interface Props {
  /** Called with the chosen security_id. */
  onSelect: (securityId: string) => void;
  onClose: () => void;
  /** If provided, used as the initial search query. */
  prefillQuery?: string;
  /**
   * Pre-loaded securities list from the parent. When non-null, skips the
   * network fetch so repeated panel opens are instant.
   */
  cachedSecurities?: SecurityMaster[] | null;
  /** Called after the first successful fetch so the parent can cache the result. */
  onCacheLoaded?: (securities: SecurityMaster[]) => void;
}

/**
 * Inline panel that lets the user search the full securities catalog and
 * select an existing entry to map to the current import question.
 *
 * Securities are fetched lazily on first mount and cached in component state
 * for the lifetime of the panel (no re-fetch on repeated opens — the parent
 * mounts/unmounts this component each time, so the cache lives in the parent).
 */
export default function SecuritySearchPanel({ onSelect, onClose, prefillQuery, cachedSecurities, onCacheLoaded }: Props) {
  const [securities, setSecurities] = useState<SecurityMaster[] | null>(cachedSecurities ?? null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [query, setQuery] = useState(prefillQuery ?? "");
  const inputRef = useRef<HTMLInputElement>(null);

  // Load catalog once on mount — skip if already cached.
  useEffect(() => {
    if (securities !== null) return; // already have data
    let cancelled = false;
    listSecurities()
      .then((res) => {
        if (!cancelled) {
          setSecurities(res.securities);
          onCacheLoaded?.(res.securities);
        }
      })
      .catch((err) => {
        if (!cancelled)
          setFetchError(err instanceof Error ? err.message : "Failed to load securities");
      });
    return () => {
      cancelled = true;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-focus input when panel opens.
  useEffect(() => {
    inputRef.current?.focus();
  }, [securities]);

  const results =
    securities !== null ? filterSecurities(securities, query) : null;

  const inputCls =
    "w-full rounded-[var(--radius)] border border-border bg-bg-input px-3 py-2 text-sm text-text placeholder:text-text-muted focus:border-accent-blue focus:outline-none";

  return (
    <div className="mt-3 rounded-[var(--radius)] border border-border/60 bg-bg-card-2 p-4 space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-text">Find in portfolio</span>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close search"
          className="text-text-muted hover:text-text text-sm leading-none"
        >
          ✕
        </button>
      </div>

      {/* Search input */}
      <div>
        <label htmlFor="security-search-input" className="sr-only">
          Search securities
        </label>
        <input
          id="security-search-input"
          ref={inputRef}
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Ticker, name, or ID…"
          disabled={securities === null && fetchError === null}
          className={inputCls}
        />
      </div>

      {/* States */}
      {fetchError !== null && (
        <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-3 py-2 text-sm text-accent-red">
          {fetchError}
        </div>
      )}

      {fetchError === null && securities === null && (
        <div className="text-xs text-text-muted animate-pulse">Loading securities…</div>
      )}

      {results !== null && results.length === 0 && query.trim() !== "" && (
        <div className="text-sm text-text-muted">No securities match &ldquo;{query}&rdquo;.</div>
      )}

      {results !== null && results.length === 0 && query.trim() === "" && (
        <div className="text-sm text-text-muted">Start typing to search.</div>
      )}

      {/* Results list */}
      {results !== null && results.length > 0 && (
        <ul className="space-y-1 max-h-60 overflow-y-auto" role="listbox" aria-label="Security results">
          {results.slice(0, 50).map((s) => (
            <li key={s.security_id} role="option" aria-selected={false}>
              <button
                type="button"
                onClick={() => onSelect(s.security_id)}
                className="w-full flex items-center justify-between gap-3 rounded-[var(--radius)] border border-border/60 bg-bg px-3 py-2 text-left text-sm hover:bg-bg-hover hover:border-accent-blue/40 transition-colors"
              >
                <span className="min-w-0">
                  <span className="font-mono font-semibold text-text mr-2">
                    {s.security_id}
                  </span>
                  <span className="text-text-muted truncate">{s.company_name}</span>
                </span>
                <span className="flex-shrink-0 flex items-center gap-2 text-xs text-text-muted">
                  <span>{s.listing_currency}</span>
                  {s.provider_symbols?.yfinance && (
                    <span className="font-mono text-text-muted/70">
                      {s.provider_symbols.yfinance}
                    </span>
                  )}
                </span>
              </button>
            </li>
          ))}
          {results.length > 50 && (
            <li className="px-3 py-1 text-xs text-text-muted">
              {results.length - 50} more — refine your search.
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
