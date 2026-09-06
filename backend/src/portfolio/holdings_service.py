"""Derived holdings computation from ledger movements.

Holdings = SUM(BUY quantities) - SUM(SELL quantities) per security.
Average cost basis uses BUY movements only (excluding zero-cost acquisitions).
Dividends are summed separately.

All arithmetic in Decimal for precision.
"""

from __future__ import annotations

import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from .cosmos_portfolio import CosmosPortfolioService
from .cosmos_securities import CosmosSecuritiesService

logger = logging.getLogger(__name__)

_TWO_PLACES = Decimal("0.01")
_SIX_PLACES = Decimal("0.000001")


def _d(v: Any) -> Decimal:
    if isinstance(v, Decimal):
        return v
    if v is None:
        return Decimal("0")
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal("0")


class HoldingsService:
    """Compute derived holdings from the portfolio ledger."""

    def __init__(
        self,
        portfolio_svc: CosmosPortfolioService,
        securities_svc: CosmosSecuritiesService,
    ) -> None:
        self.portfolio_svc = portfolio_svc
        self.securities_svc = securities_svc

    def compute_holdings(
        self, account_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Compute holdings from all non-deleted ledger movements.

        Args:
            account_id: Filter to specific account; None = all accounts.

        Returns:
            Dict with 'holdings' list and 'summary' dict.
        """
        movements = self.portfolio_svc.get_all_movements_for_holdings()

        # Filter by account if requested
        if account_id:
            movements = [
                m for m in movements if m.get("account_id") == account_id
            ]

        # Aggregate per security
        per_security: Dict[str, Dict[str, Any]] = {}

        for m in movements:
            security_id = m.get("security_id", "")
            if not security_id:
                continue
            if security_id not in per_security:
                per_security[security_id] = {
                    "security_id": security_id,
                    "ticker": m.get("ticker", security_id.split(":")[-1]),
                    "total_shares": Decimal("0"),
                    "total_cost_eur": Decimal("0"),      # BUY + TRANSFER_IN carried basis
                    "total_buy_cost_eur": Decimal("0"),  # BUY outflows only (not TRANSFER_IN)
                    "paid_buy_shares": Decimal("0"),
                    "total_dividends_eur": Decimal("0"),
                    "total_sales_eur": Decimal("0"),
                    "buy_count": 0,
                    "zero_cost_count": 0,
                    "accounts": set(),
                    "movement_warnings": [],
                }
            agg = per_security[security_id]
            agg["accounts"].add(m.get("account_id", "_unassigned"))

            qty = _d(m.get("quantity", "0"))
            gross_eur = _d((m.get("gross") or {}).get("eur_amount", "0"))
            commission_eur = _d((m.get("fees") or {}).get("total_eur", "0"))
            cost_basis_status = m.get("cost_basis_status", "COMPLETE")

            txn_type = m.get("txn_type", "")
            if txn_type == "BUY":
                agg["total_shares"] += qty
                if cost_basis_status != "INCOMPLETE":
                    cost = gross_eur + commission_eur
                    agg["total_cost_eur"] += cost
                    agg["total_buy_cost_eur"] += cost  # BUY-only accumulator
                    agg["paid_buy_shares"] += qty
                else:
                    agg["zero_cost_count"] += 1
                agg["buy_count"] += 1
            elif txn_type == "SELL":
                # DERECHOS sales contribute to proceeds but do NOT decrement share count.
                # Existing movements without sales_type default to ACCIONES behaviour.
                sale_type = m.get("sales_type") or "ACCIONES"
                if sale_type == "ACCIONES":
                    agg["total_shares"] -= qty
                agg["total_sales_eur"] += gross_eur - commission_eur
            elif txn_type == "DIVIDEND":
                net_eur = _d((m.get("net") or {}).get("eur_amount", "0"))
                agg["total_dividends_eur"] += net_eur
            elif txn_type == "TRANSFER_IN":
                # Adds shares to the destination account; carries cost basis; not a purchase.
                agg["total_shares"] += qty
                carried_cost = _d(m.get("transfer_cost_basis_eur", "0"))
                agg["total_cost_eur"] += carried_cost
                if qty > Decimal("0") and carried_cost > Decimal("0"):
                    agg["paid_buy_shares"] += qty
            elif txn_type == "TRANSFER_OUT":
                # Subtracts shares and proportional cost from source account.
                agg["total_shares"] -= qty
                carried_cost = _d(m.get("transfer_cost_basis_eur", "0"))
                agg["total_cost_eur"] = max(Decimal("0"), agg["total_cost_eur"] - carried_cost)
                if qty > Decimal("0"):
                    agg["paid_buy_shares"] = max(Decimal("0"), agg["paid_buy_shares"] - qty)

            # Carry per-movement warnings to holding warnings
            for w in m.get("warnings", []):
                agg["movement_warnings"].append(w)
        security_names = _resolve_security_names(
            list(per_security.keys()), self.securities_svc
        )

        # Build holdings list
        holdings_list = []
        total_invested = Decimal("0")
        total_dividends = Decimal("0")
        total_purchases = Decimal("0")
        total_sales = Decimal("0")

        for security_id, agg in per_security.items():
            total_shares = agg["total_shares"]
            total_cost = agg["total_cost_eur"]      # BUY + TRANSFER_IN basis
            buy_cost = agg["total_buy_cost_eur"]    # BUY outflows only
            paid_shares = agg["paid_buy_shares"]

            # Average cost basis: total paid cost / total paid shares
            avg_cost: Optional[Decimal] = None
            if paid_shares > Decimal("0") and total_cost > Decimal("0"):
                avg_cost = (total_cost / paid_shares).quantize(
                    _TWO_PLACES, rounding=ROUND_HALF_UP
                )

            # Cost basis status
            zero_cost_count = agg["zero_cost_count"]
            if zero_cost_count > 0:
                cost_basis_status = "INCOMPLETE"
            else:
                cost_basis_status = "COMPLETE"

            # Per-holding warnings
            item_warnings = []
            if total_shares < Decimal("0"):
                item_warnings.append({
                    "type": "NEGATIVE_INVENTORY",
                    "message": (
                        "Negative holdings — earlier purchases may not yet be imported"
                    ),
                })
            if zero_cost_count > 0:
                item_warnings.append({
                    "type": "ZERO_COST_ACQUISITION",
                    "count": zero_cost_count,
                    "message": (
                        f"{zero_cost_count} acquisition(s) with incomplete cost basis"
                    ),
                })

            total_invested += total_cost
            total_purchases += buy_cost   # BUY outflows only, excludes TRANSFER_IN
            total_sales += agg["total_sales_eur"]
            total_dividends += agg["total_dividends_eur"]

            company_name = security_names.get(security_id, "")
            ticker = agg["ticker"]

            holdings_list.append({
                "security_id": security_id,
                "ticker": ticker,
                "company_name": company_name,
                "total_shares": str(
                    total_shares.quantize(_SIX_PLACES, rounding=ROUND_HALF_UP)
                ),
                "avg_cost_basis_eur": (
                    str(avg_cost.quantize(_TWO_PLACES)) if avg_cost is not None else None
                ),
                "cost_basis_status": cost_basis_status,
                "total_invested_eur": str(total_cost.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)),
                "total_purchases_eur": str(buy_cost.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)),
                "total_sales_eur": str(
                    agg["total_sales_eur"].quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
                ),
                "total_dividends_eur": str(
                    agg["total_dividends_eur"].quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
                ),
                "accounts": sorted(agg["accounts"]),
                "warnings": item_warnings,
            })

        # Sort: negative holdings last, then by security_id
        holdings_list.sort(
            key=lambda h: (
                Decimal(h["total_shares"]) < Decimal("0"),
                h["security_id"],
            )
        )

        return {
            "holdings": holdings_list,
            "summary": {
                "total_securities": len(holdings_list),
                "total_invested_eur": str(
                    total_invested.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
                ),
                "total_purchases_eur": str(
                    total_purchases.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
                ),
                "total_sales_eur": str(
                    total_sales.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
                ),
                "current_invested_eur": str(
                    (total_invested - total_sales).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
                ),
                "total_dividends_eur": str(
                    total_dividends.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
                ),
            },
        }


def _resolve_security_names(
    security_ids: List[str],
    securities_svc: CosmosSecuritiesService,
) -> Dict[str, str]:
    """Batch-resolve company names for a list of security IDs."""
    result: Dict[str, str] = {}
    for sid in security_ids:
        try:
            sec = securities_svc.get_security(sid)
            if sec:
                result[sid] = sec.get("company_name", "")
        except Exception as exc:
            logger.debug("Could not resolve security name for %s: %s", sid, exc)
    return result
