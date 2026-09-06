"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

interface DetailSectionProps {
  title: string;
  badge?: string | number;
  defaultOpen?: boolean;
  children: React.ReactNode;
}

/**
 * Reusable collapsible section container for the Symbol Detail page.
 * Used for Options and Stocks sections (Amendment I).
 */
export default function DetailSection({
  title,
  badge,
  defaultOpen = true,
  children,
}: DetailSectionProps) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className="rounded-[var(--radius)] border border-border bg-bg-card">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-5 py-3 text-left hover:bg-bg-hover transition-colors rounded-[var(--radius)]"
        aria-expanded={open}
      >
        <div className="flex items-center gap-2">
          <span className="text-base font-semibold text-text">{title}</span>
          {badge !== undefined && (
            <span className="rounded-full bg-bg-hover px-2 py-0.5 text-xs font-medium text-text-muted">
              {badge}
            </span>
          )}
        </div>
        {open ? (
          <ChevronDown size={16} className="text-text-muted shrink-0" />
        ) : (
          <ChevronRight size={16} className="text-text-muted shrink-0" />
        )}
      </button>

      {open && (
        <div className="border-t border-border px-5 pb-5 pt-4 space-y-5">
          {children}
        </div>
      )}
    </div>
  );
}
