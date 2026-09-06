"use client";

import type { BrokerAccount } from "@/types/portfolio";
import { getAccountLabel, getAccountBadgeClass } from "@/lib/accountDisplay";

interface AccountBadgeProps {
  accountId: string;
  accounts: BrokerAccount[];
  className?: string;
}

/**
 * Renders an account as a colored badge showing "Broker · Name".
 * Color is deterministic and stable for the given account_id.
 * Always includes text — never color-only.
 */
export default function AccountBadge({ accountId, accounts, className = "" }: AccountBadgeProps) {
  const label = getAccountLabel(accountId, accounts);
  const colorCls = getAccountBadgeClass(accountId);
  return (
    <span
      className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${colorCls} ${className}`.trim()}
    >
      {label}
    </span>
  );
}
