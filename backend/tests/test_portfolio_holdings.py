"""Tests for holdings derivation (HoldingsService).

Hermetic: fake Cosmos containers, no network.
"""

import pytest
from decimal import Decimal

from src.portfolio.cosmos_portfolio import CosmosPortfolioService
from src.portfolio.cosmos_securities import CosmosSecuritiesService
from src.portfolio.holdings_service import HoldingsService


# ---------------------------------------------------------------------------
# Fake Cosmos containers
# ---------------------------------------------------------------------------

class FakePortfolioContainer:
    def __init__(self, movements=None):
        self._movements = movements or []

    def query_items(self, query="", parameters=None, enable_cross_partition_query=True, partition_key=None):
        if "COUNT" in query:
            return iter([len(self._movements)])
        if "NOT IS_DEFINED(c.deleted_at)" in query:
            result = [m for m in self._movements if "deleted_at" not in m]
            return iter(result)
        return iter(list(self._movements))

    def upsert_item(self, body):
        self._movements.append(body)
        return body

    def read_item(self, item, partition_key):
        from azure.cosmos.exceptions import CosmosResourceNotFoundError
        for m in self._movements:
            if m.get("id") == item:
                return dict(m)
        raise CosmosResourceNotFoundError(message="not found", response=None)

    def replace_item(self, item, body):
        for i, m in enumerate(self._movements):
            if m.get("id") == item:
                self._movements[i] = dict(body)
                return dict(body)
        from azure.cosmos.exceptions import CosmosResourceNotFoundError
        raise CosmosResourceNotFoundError(message="not found", response=None)


class FakeSymbolsContainer:
    def __init__(self):
        self._store = {}

    def read_item(self, item, partition_key):
        from azure.cosmos.exceptions import CosmosResourceNotFoundError
        key = (partition_key, item)
        if key not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        return dict(self._store[key])

    def create_item(self, body):
        ticker = body["symbol"]
        key = (ticker, body["id"])
        self._store[key] = dict(body)
        return dict(body)

    def query_items(self, query="", parameters=None, enable_cross_partition_query=False, partition_key=None):
        return iter([v for v in self._store.values() if v.get("doc_type") == "security_master"])

    def replace_item(self, item, body):
        for key in self._store:
            if self._store[key].get("id") == item:
                self._store[key] = dict(body)
                return dict(body)
        from azure.cosmos.exceptions import CosmosResourceNotFoundError
        raise CosmosResourceNotFoundError(message="not found", response=None)


def _make_movement(
    movement_id, security_id, txn_type, quantity, gross_eur,
    account_id="_unassigned", commission_eur="0", net_eur=None,
    cost_basis_status="COMPLETE", trade_date="2024-01-15",
):
    ticker = security_id.split(":")[-1]
    net = net_eur or str(Decimal(gross_eur) - Decimal(commission_eur))
    return {
        "id": movement_id,
        "doc_type": "ledger_txn",
        "txn_type": txn_type,
        "security_id": security_id,
        "ticker": ticker,
        "trade_date": trade_date,
        "quantity": str(quantity),
        "gross": {"amount": gross_eur, "currency": "EUR", "eur_amount": gross_eur},
        "fees": {"total": commission_eur, "currency": "EUR", "total_eur": commission_eur},
        "net": {"amount": net, "currency": "EUR", "eur_amount": net},
        "account_id": account_id,
        "cost_basis_status": cost_basis_status,
        "warnings": [],
    }


def _make_services(movements):
    portfolio_svc = CosmosPortfolioService(
        FakePortfolioContainer(movements), None
    )
    securities_svc = CosmosSecuritiesService(FakeSymbolsContainer())
    return portfolio_svc, securities_svc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEmptyLedger:
    def test_empty_holdings(self):
        portfolio_svc, securities_svc = _make_services([])
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        assert result["holdings"] == []
        assert result["summary"]["total_securities"] == 0
        assert result["summary"]["total_invested_eur"] == "0.00"
        assert result["summary"]["total_dividends_eur"] == "0.00"


class TestShareCalculation:
    def test_buy_only(self):
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "100", "18250.00", commission_eur="7.50"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        assert len(result["holdings"]) == 1
        h = result["holdings"][0]
        assert Decimal(h["total_shares"]) == Decimal("100")
        assert h["security_id"] == "XNYS:AAPL"

    def test_buy_then_sell(self):
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "100", "18250.00"),
            _make_movement("t2", "XNYS:AAPL", "SELL", "30", "5800.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert Decimal(h["total_shares"]) == Decimal("70")

    def test_sell_before_buy_gives_negative_inventory(self):
        """Sales before buys are accepted with NEGATIVE_INVENTORY warning."""
        movements = [
            _make_movement("t1", "XNYS:AAPL", "SELL", "50", "9000.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert Decimal(h["total_shares"]) == Decimal("-50")
        warning_types = [w["type"] for w in h["warnings"]]
        assert "NEGATIVE_INVENTORY" in warning_types

    def test_dividend_counted_separately(self):
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "100", "18250.00"),
            _make_movement(
                "t2", "XNYS:AAPL", "DIVIDEND", "0", "86.25",
                net_eur="73.31",
            ),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert Decimal(h["total_shares"]) == Decimal("100")
        assert Decimal(h["total_dividends_eur"]) == Decimal("73.31")


class TestCostBasis:
    def test_complete_cost_basis(self):
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "10", "1825.00", commission_eur="7.50"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert h["cost_basis_status"] == "COMPLETE"
        assert h["avg_cost_basis_eur"] is not None

    def test_zero_cost_acquisition_incomplete(self):
        movements = [
            _make_movement(
                "t1", "XNYS:AAPL", "BUY", "50", "0", cost_basis_status="INCOMPLETE"
            ),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert h["cost_basis_status"] == "INCOMPLETE"
        warning_types = [w["type"] for w in h["warnings"]]
        assert "ZERO_COST_ACQUISITION" in warning_types

    def test_total_invested_excludes_dividends(self):
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "10", "1825.00"),
            _make_movement("t2", "XNYS:AAPL", "DIVIDEND", "0", "100.00", net_eur="85.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert Decimal(h["total_invested_eur"]) == Decimal("1825.00")
        assert Decimal(h["total_dividends_eur"]) == Decimal("85.00")


class TestMultipleAccounts:
    def test_each_account_tracked(self):
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "10", "1000.00", account_id="ibkr"),
            _make_movement("t2", "XNYS:AAPL", "BUY", "20", "2000.00", account_id="_unassigned"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert Decimal(h["total_shares"]) == Decimal("30")
        assert "ibkr" in h["accounts"]
        assert "_unassigned" in h["accounts"]

    def test_filter_by_account(self):
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "10", "1000.00", account_id="ibkr"),
            _make_movement("t2", "XNYS:AAPL", "BUY", "20", "2000.00", account_id="_unassigned"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings(account_id="ibkr")
        assert len(result["holdings"]) == 1
        h = result["holdings"][0]
        assert Decimal(h["total_shares"]) == Decimal("10")


class TestMultipleSecurities:
    def test_separate_holdings(self):
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "10", "1000.00"),
            _make_movement("t2", "XMAD:TEF", "BUY", "100", "380.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        ids = {h["security_id"] for h in result["holdings"]}
        assert ids == {"XNYS:AAPL", "XMAD:TEF"}
        assert result["summary"]["total_securities"] == 2

    def test_summary_totals(self):
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "10", "1000.00"),
            _make_movement("t2", "XMAD:TEF", "BUY", "100", "380.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        assert Decimal(result["summary"]["total_invested_eur"]) == Decimal("1380.00")


# ---------------------------------------------------------------------------
# F1 — Commission (fees.total_eur) included in holdings cost basis
# ---------------------------------------------------------------------------

class TestCommissionInCostBasis:
    def test_commission_affects_holdings_cost_basis(self):
        movements = [
            _make_movement(
                "t1", "XNYS:AAPL", "BUY", "10", "1000.00",
                commission_eur="10.00",
            ),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        # Total(EUR)=1000 + Comision=10 => total_invested_eur==1010
        assert Decimal(h["total_invested_eur"]) == Decimal("1010.00")

    def test_zero_commission_no_effect(self):
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "10", "1825.00", commission_eur="0"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert Decimal(h["total_invested_eur"]) == Decimal("1825.00")


# ---------------------------------------------------------------------------
# F5 — Holdings total_shares unaffected by dividend with null quantity
# ---------------------------------------------------------------------------

class TestDividendQuantityNullHoldings:
    def test_holdings_ignores_dividend_quantity(self):
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "100", "18250.00"),
            {
                "id": "t2",
                "doc_type": "ledger_txn",
                "txn_type": "DIVIDEND",
                "security_id": "XNYS:AAPL",
                "ticker": "AAPL",
                "trade_date": "2024-06-15",
                "quantity": None,
                "gross": {"amount": "100.00", "currency": "EUR", "eur_amount": "100.00"},
                "fees": {"total": "0.00", "currency": "EUR", "total_eur": "0.00"},
                "net": {"amount": "73.31", "currency": "EUR", "eur_amount": "73.31"},
                "account_id": "_unassigned",
                "cost_basis_status": "COMPLETE",
                "warnings": [],
            },
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert Decimal(h["total_shares"]) == Decimal("100")
        assert Decimal(h["total_dividends_eur"]) == Decimal("73.31")


# ---------------------------------------------------------------------------
# F7 — avg_cost_basis_eur must divide by paid shares, not transaction count
# ---------------------------------------------------------------------------

class TestAvgCostBasisPerShare:
    def test_avg_cost_basis_single_buy(self):
        """1 BUY: 10 shares, gross=€1825, commission=€7.50 → avg = 1832.50/10 = 183.25."""
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "10", "1825.00", commission_eur="7.50"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert h["avg_cost_basis_eur"] == "183.25"

    def test_avg_cost_basis_multi_buy(self):
        """BUY 100@€1000 (€10 fee) + BUY 50@€750 (€5 fee) → avg = (1010+755)/150 = 11.77."""
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "100", "1000.00", commission_eur="10.00"),
            _make_movement("t2", "XNYS:AAPL", "BUY", "50", "750.00", commission_eur="5.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert h["avg_cost_basis_eur"] == "11.77"

    def test_avg_cost_basis_excludes_zero_cost(self):
        """Paid BUY (100 shares, €1000, €10 fee) + INCOMPLETE BUY (50 shares) → avg = 1010/100 = 10.10."""
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "100", "1000.00", commission_eur="10.00"),
            _make_movement("t2", "XNYS:AAPL", "BUY", "50", "0", cost_basis_status="INCOMPLETE"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert h["avg_cost_basis_eur"] == "10.10"

    def test_avg_cost_basis_no_paid_buys_is_null(self):
        """Only INCOMPLETE BUYs → avg_cost_basis_eur is None."""
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "50", "0", cost_basis_status="INCOMPLETE"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert h["avg_cost_basis_eur"] is None

    def test_avg_cost_basis_independent_of_sells(self):
        """BUY 100@€1000 (€10 fee) then SELL 30 → avg = 1010/100 = 10.10 (sell does not change it)."""
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "100", "1000.00", commission_eur="10.00"),
            _make_movement("t2", "XNYS:AAPL", "SELL", "30", "350.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert h["avg_cost_basis_eur"] == "10.10"
        assert Decimal(h["total_shares"]) == Decimal("70")

    def test_avg_cost_basis_dividends_only_is_null(self):
        """Only DIVIDEND movements → avg_cost_basis_eur is None."""
        movements = [
            _make_movement("t1", "XNYS:AAPL", "DIVIDEND", "0", "100.00", net_eur="85.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert h["avg_cost_basis_eur"] is None


# ---------------------------------------------------------------------------
# Summary totals — purchases, sales, current_invested, backward-compat alias
# ---------------------------------------------------------------------------

class TestSummaryTotals:
    def test_purchases_only(self):
        """BUY 100@€1000 (€10 fee) → purchases=1010, sales=0, current_invested=1010."""
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "100", "1000.00", commission_eur="10.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        s = result["summary"]
        assert s["total_purchases_eur"] == "1010.00"
        assert s["total_sales_eur"] == "0.00"
        assert s["current_invested_eur"] == "1010.00"

    def test_purchases_and_sales(self):
        """BUY 100@€1000 (€10 fee) + SELL 30@€350 (€5 fee) → purchases=1010, sales=345, current=665."""
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "100", "1000.00", commission_eur="10.00"),
            _make_movement("t2", "XNYS:AAPL", "SELL", "30", "350.00", commission_eur="5.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        s = result["summary"]
        assert s["total_purchases_eur"] == "1010.00"
        assert s["total_sales_eur"] == "345.00"
        assert s["current_invested_eur"] == "665.00"

    def test_sales_exceed_purchases(self):
        """BUY 100@€500 (€0 fee) + SELL 100@€800 (€0 fee) → current_invested=-300.00 (profit)."""
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "100", "500.00"),
            _make_movement("t2", "XNYS:AAPL", "SELL", "100", "800.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        s = result["summary"]
        assert s["total_purchases_eur"] == "500.00"
        assert s["total_sales_eur"] == "800.00"
        assert s["current_invested_eur"] == "-300.00"

    def test_dividends_excluded_from_current_invested(self):
        """BUY + DIVIDEND → current_invested_eur equals total_purchases_eur (dividend excluded)."""
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "100", "1000.00", commission_eur="10.00"),
            _make_movement("t2", "XNYS:AAPL", "DIVIDEND", "0", "86.25", net_eur="73.31"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        s = result["summary"]
        assert s["current_invested_eur"] == s["total_purchases_eur"]
        assert s["total_purchases_eur"] == "1010.00"

    def test_incomplete_buys_excluded_from_purchases(self):
        """INCOMPLETE BUY → total_purchases_eur=0.00."""
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "50", "0", cost_basis_status="INCOMPLETE"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        s = result["summary"]
        assert s["total_purchases_eur"] == "0.00"
        assert s["total_sales_eur"] == "0.00"
        assert s["current_invested_eur"] == "0.00"

    def test_multi_security_aggregation(self):
        """2 securities, mixed buys/sells → summary totals are portfolio-wide sums."""
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "10", "1000.00", commission_eur="5.00"),
            _make_movement("t2", "XNYS:AAPL", "SELL", "5", "600.00", commission_eur="3.00"),
            _make_movement("t3", "XMAD:TEF", "BUY", "100", "400.00", commission_eur="2.00"),
            _make_movement("t4", "XMAD:TEF", "SELL", "20", "90.00", commission_eur="1.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        s = result["summary"]
        # AAPL purchases: 1000+5=1005; TEF purchases: 400+2=402 → 1407
        assert Decimal(s["total_purchases_eur"]) == Decimal("1407.00")
        # AAPL sales: 600-3=597; TEF sales: 90-1=89 → 686
        assert Decimal(s["total_sales_eur"]) == Decimal("686.00")
        assert Decimal(s["current_invested_eur"]) == Decimal("721.00")
        assert s["total_securities"] == 2

    def test_backward_compat_total_invested(self):
        """summary.total_invested_eur == summary.total_purchases_eur (backward-compat alias)."""
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "100", "1000.00", commission_eur="10.00"),
            _make_movement("t2", "XNYS:AAPL", "SELL", "30", "350.00", commission_eur="5.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        s = result["summary"]
        assert s["total_invested_eur"] == s["total_purchases_eur"]

    def test_empty_ledger_new_fields(self):
        """Empty ledger → all new summary fields are zero strings."""
        portfolio_svc, securities_svc = _make_services([])
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        s = result["summary"]
        assert s["total_purchases_eur"] == "0.00"
        assert s["total_sales_eur"] == "0.00"
        assert s["current_invested_eur"] == "0.00"

    def test_soft_deleted_excluded(self):
        """Movements with deleted_at are excluded from all totals."""
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "100", "1000.00", commission_eur="10.00"),
            {
                **_make_movement("t2", "XNYS:AAPL", "BUY", "50", "500.00", commission_eur="5.00"),
                "deleted_at": "2024-03-01T00:00:00Z",
            },
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        s = result["summary"]
        # Only t1 should be counted (t2 is soft-deleted)
        assert s["total_purchases_eur"] == "1010.00"


# ---------------------------------------------------------------------------
# Per-security totals — total_purchases_eur and total_sales_eur on each holding
# ---------------------------------------------------------------------------

class TestPerSecurityTotals:
    def test_per_security_purchases_and_sales(self):
        """Per-holding total_purchases_eur and total_sales_eur are correct."""
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "100", "18250.00", commission_eur="7.50"),
            _make_movement("t2", "XNYS:AAPL", "SELL", "30", "5500.00", commission_eur="5.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        # purchases = 18250 + 7.50 = 18257.50
        assert h["total_purchases_eur"] == "18257.50"
        # purchases alias equals total_invested_eur
        assert h["total_purchases_eur"] == h["total_invested_eur"]
        # sales = 5500 - 5 = 5495.00
        assert h["total_sales_eur"] == "5495.00"

    def test_per_security_no_sells(self):
        """Per-holding total_sales_eur is '0.00' when there are no sells."""
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "50", "5000.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        h = result["holdings"][0]
        assert h["total_sales_eur"] == "0.00"

    def test_per_security_independent_of_other_securities(self):
        """Each security's sales/purchases are isolated from other securities."""
        movements = [
            _make_movement("t1", "XNYS:AAPL", "BUY", "10", "1000.00", commission_eur="5.00"),
            _make_movement("t2", "XNYS:AAPL", "SELL", "5", "600.00", commission_eur="3.00"),
            _make_movement("t3", "XMAD:TEF", "BUY", "100", "400.00", commission_eur="2.00"),
        ]
        portfolio_svc, securities_svc = _make_services(movements)
        svc = HoldingsService(portfolio_svc, securities_svc)
        result = svc.compute_holdings()
        by_id = {h["security_id"]: h for h in result["holdings"]}
        assert by_id["XNYS:AAPL"]["total_purchases_eur"] == "1005.00"
        assert by_id["XNYS:AAPL"]["total_sales_eur"] == "597.00"
        assert by_id["XMAD:TEF"]["total_purchases_eur"] == "402.00"
        assert by_id["XMAD:TEF"]["total_sales_eur"] == "0.00"
