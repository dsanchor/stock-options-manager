import Link from "next/link";
import { symbolHref } from "@/lib/symbolEncoding";
import type { DisambiguationChoice } from "@/types/symbol-detail";

interface Props {
  query: string;
  choices: DisambiguationChoice[];
}

export default function SymbolDisambiguation({ query, choices }: Props) {
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Multiple matches for &ldquo;{query}&rdquo;</h1>
        <p className="mt-1 text-sm text-text-muted">
          Several securities share this ticker. Select the one you want to view.
        </p>
      </div>
      <div className="surface rounded-[var(--radius)] border border-border overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted">
              <th className="px-4 py-3 font-medium">Company</th>
              <th className="px-4 py-3 font-medium">Exchange (MIC)</th>
              <th className="px-4 py-3 font-medium text-right">Action</th>
            </tr>
          </thead>
          <tbody>
            {choices.map((c) => (
              <tr key={c.security_id} className="border-b border-border/40 last:border-0 hover:bg-bg-hover/50 transition-colors">
                <td className="px-4 py-3">
                  <span className="font-semibold text-text">{c.company_name}</span>
                  <span className="ml-2 font-mono text-xs text-text-muted">{c.security_id}</span>
                </td>
                <td className="px-4 py-3 font-mono text-text-muted">{c.exchange_mic}</td>
                <td className="px-4 py-3 text-right">
                  <Link
                    href={symbolHref(c.security_id)}
                    className="inline-flex items-center gap-1 rounded-[var(--radius-pill)] bg-accent-blue px-3 py-1 text-xs font-medium text-white hover:opacity-90 transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-blue/60"
                  >
                    View →
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
