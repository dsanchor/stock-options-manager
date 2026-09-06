"""Tests for total_shares reconciliation report — Symbol Unification rev 3.

Contract: Danny's §7 — total_shares Reconciliation Report Only.

Coverage:
- Report endpoint returns 200 with correct structure
- Matched symbols detected correctly
- Mismatched symbols detected correctly
- No-portfolio-data handling
- Zero writes: endpoint is read-only; no Cosmos writes
- Summary counts accurate (total, matched, mismatched, no_portfolio_data)

Uses FastAPI TestClient with fake Cosmos state.
"""

from __future__ import annotations

import pytest
from decimal import Decimal
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fake containers
# ---------------------------------------------------------------------------

class FakePortfolioContainer:
    def __init__(self, docs=None):
        self._store: dict = {}
        for doc in (docs or []):
            self._store[doc["id"]] = dict(doc)

    def read(self):
        return {}

    def create_item(self, body):
        self._store[body["id"]] = dict(body)
        return dict(body)

    def upsert_item(self, body):
        self._store[body["id"]] = dict(body)
        return dict(body)

    def read_item(self, item, partition_key):
        if item not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        return dict(self._store[item])

    def replace_item(self, item, body):
        self._store[item] = dict(body)
        return dict(body)

    def query_items(self, query="", parameters=None, enable_cross_partition_query=True,
                    partition_key=None):
        param_map = {p["name"]: p["value"] for p in (parameters or [])}
        results = []
        for doc in self._store.values():
            if "doc_type = 'ledger_txn'" in query and doc.get("doc_type") != "ledger_txn":
                continue
            if "NOT IS_DEFINED(c.deleted_at)" in query and "deleted_at" in doc:
                continue
            if ("NOT IS_DEFINED(c.correction_status) OR c.correction_status = 'ACTIVE'") in query:
                cs = doc.get("correction_status")
                if cs is not None and cs not in ("ACTIVE",):
                    continue
            if "@security_id" in param_map and doc.get("security_id") != param_map["@security_id"]:
                continue
            results.append(dict(doc))
        return iter(results)

    def _write_count(self):
        return getattr(self, "_write_calls", 0)


class FakeSymbolsContainer:
    def __init__(self):
        self._store: dict = {}
        self._write_calls = 0

    def read_item(self, item, partition_key):
        key = (partition_key, item)
        if key not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        return dict(self._store[key])

    def create_item(self, body):
        self._write_calls += 1
        ticker = body["symbol"]
        key = (ticker, body["id"])
        self._store[key] = dict(body)
        return dict(body)

    def replace_item(self, item, body):
        self._write_calls += 1
        for key in self._store:
            if self._store[key].get("id") == item:
                self._store[key] = dict(body)
                return dict(body)
        raise CosmosResourceNotFoundError(message="not found", response=None)

    def upsert_item(self, body):
        self._write_calls += 1
        ticker = body.get("symbol", "")
        key = (ticker, body["id"])
        self._store[key] = dict(body)
        return dict(body)

    def query_items(self, query="", parameters=None, enable_cross_partition_query=False,
                    partition_key=None):
        # Return matching docs based on query filters
        if "doc_type = 'symbol_config'" in query:
            return iter([
                doc for (pk, did), doc in self._store.items()
                if doc.get("doc_type") == "symbol_config"
            ])
        return iter([
            doc for (pk, did), doc in self._store.items()
            if doc.get("doc_type") == "security_master"
        ])

    def seed_security(self, security_id: str, company_name: str = "Test Co"):
        mic, ticker = security_id.split(":", 1)
        doc = {
            "id": f"sec_{mic}_{ticker}",
            "symbol": ticker,
            "doc_type": "security_master",
            "security_id": security_id,
            "ticker": ticker,
            "company_name": company_name,
            "exchange_mic": mic,
            "status": "ACTIVE",
        }
        self._store[(ticker, doc["id"])] = doc

    def seed_config(self, ticker: str, total_shares: int = 0,
                    extra: dict | None = None) -> dict:
        doc = {
            "id": f"config_{ticker}",
            "symbol": ticker,
            "doc_type": "symbol_config",
            "total_shares": total_shares,
            "telegram_notifications_enabled": False,
            "watchlist": {"covered_call": False, "cash_secured_put": False, "buy_tracker": False},
            "positions": [],
        }
        if extra:
            doc.update(extra)
        self._store[(ticker, doc["id"])] = doc
        return doc


class FakeCosmos:
    def __init__(self, portfolio_container=None, symbols_container=None):
        self.container = symbols_container or FakeSymbolsContainer()
        self.portfolio_container = portfolio_container or FakePortfolioContainer()
        self.import_sessions_container = None

    def list_symbols(self):
        return [
            doc for (pk, did), doc in self.container._store.items()
            if doc.get("doc_type") == "symbol_config"
        ]

    def get_symbol(self, symbol):
        return None


@pytest.fixture
def client():
    from web.app import app
    fake_cosmos = FakeCosmos()
    with TestClient(app) as c:
        app.state.cosmos = fake_cosmos
        app.state.cosmos_error = None
        yield c, fake_cosmos


def _add_buy(fake_cosmos, security_id: str, quantity: str = "100", gross_eur: str = "10000"):
    ticker = security_id.split(":")[-1]
    doc = {
        "id": f"txn_{ticker}_buy",
        "account_id": "_unassigned",
        "doc_type": "ledger_txn",
        "txn_type": "BUY",
        "security_id": security_id,
        "ticker": ticker,
        "trade_date": "2026-01-01",
        "quantity": quantity,
        "gross": {"amount": gross_eur, "currency": "EUR", "eur_amount": gross_eur},
        "fees": {"total": "0", "currency": "EUR", "total_eur": "0"},
        "net_eur": gross_eur,
        "correction_status": "ACTIVE",
        "cost_basis_status": "COMPLETE",
    }
    fake_cosmos.portfolio_container._store[doc["id"]] = doc


def _add_sell(fake_cosmos, security_id: str, quantity: str):
    ticker = security_id.split(":")[-1]
    doc = {
        "id": f"txn_{ticker}_sell",
        "account_id": "_unassigned",
        "doc_type": "ledger_txn",
        "txn_type": "SELL",
        "security_id": security_id,
        "ticker": ticker,
        "trade_date": "2026-06-01",
        "quantity": quantity,
        "sales_type": "ACCIONES",
        "gross": {"amount": "5000", "currency": "EUR", "eur_amount": "5000"},
        "fees": {"total": "0", "currency": "EUR", "total_eur": "0"},
        "net_eur": "5000",
        "correction_status": "ACTIVE",
    }
    fake_cosmos.portfolio_container._store[doc["id"]] = doc


# ---------------------------------------------------------------------------
# §7 — Reconciliation report
# ---------------------------------------------------------------------------

class TestTotalSharesReconciliation:
    def test_endpoint_returns_200(self, client):
        c, fake = client
        resp = c.get("/api/admin/total-shares-reconciliation")
        assert resp.status_code == 200

    def test_response_has_reconciliation_array(self, client):
        c, fake = client
        resp = c.get("/api/admin/total-shares-reconciliation")
        data = resp.json()
        assert "reconciliation" in data
        assert isinstance(data["reconciliation"], list)

    def test_response_has_summary(self, client):
        c, fake = client
        resp = c.get("/api/admin/total-shares-reconciliation")
        data = resp.json()
        assert "summary" in data
        summary = data["summary"]
        assert "total_symbols" in summary
        assert "matched" in summary
        assert "mismatched" in summary
        assert "no_portfolio_data" in summary

    def test_match_detected_correctly(self, client):
        """config.total_shares matches portfolio-derived → status=match."""
        c, fake = client
        fake.container.seed_config("AAPL", total_shares=100)
        _add_buy(fake, "XNYS:AAPL", quantity="100", gross_eur="10000")

        resp = c.get("/api/admin/total-shares-reconciliation")
        data = resp.json()
        aapl_row = next(
            (r for r in data["reconciliation"] if r["ticker"] == "AAPL"), None
        )
        assert aapl_row is not None
        assert aapl_row["status"] == "match"
        assert Decimal(aapl_row["delta"]) == Decimal("0")

    def test_mismatch_detected_correctly(self, client):
        """config.total_shares ≠ portfolio-derived → status=mismatch with delta."""
        c, fake = client
        # Config says 50 but portfolio has 100
        fake.container.seed_config("AAPL", total_shares=50)
        _add_buy(fake, "XNYS:AAPL", quantity="100", gross_eur="10000")

        resp = c.get("/api/admin/total-shares-reconciliation")
        data = resp.json()
        aapl_row = next(
            (r for r in data["reconciliation"] if r["ticker"] == "AAPL"), None
        )
        assert aapl_row is not None
        assert aapl_row["status"] == "mismatch"
        assert Decimal(aapl_row["delta"]) != Decimal("0")

    def test_no_portfolio_data_handling(self, client):
        """Config with no ledger history → status=no_portfolio_data."""
        c, fake = client
        fake.container.seed_config("MSFT", total_shares=200)
        # No ledger docs for MSFT

        resp = c.get("/api/admin/total-shares-reconciliation")
        data = resp.json()
        msft_row = next(
            (r for r in data["reconciliation"] if r["ticker"] == "MSFT"), None
        )
        assert msft_row is not None
        assert msft_row["status"] == "no_portfolio_data"

    def test_no_writes_made(self, client):
        """Reconciliation report is read-only — zero writes to any container."""
        c, fake = client
        fake.container.seed_config("AAPL", total_shares=50)
        _add_buy(fake, "XNYS:AAPL", quantity="100")
        writes_before = fake.container._write_calls

        c.get("/api/admin/total-shares-reconciliation")

        assert fake.container._write_calls == writes_before, (
            "Reconciliation report must not make any Cosmos writes"
        )

    def test_summary_counts_accurate(self, client):
        c, fake = client
        # AAPL: match (100 = 100)
        fake.container.seed_config("AAPL", total_shares=100)
        _add_buy(fake, "XNYS:AAPL", quantity="100", gross_eur="10000")
        # MSFT: no portfolio data
        fake.container.seed_config("MSFT", total_shares=50)
        # TEF: mismatch (50 ≠ 75)
        fake.container.seed_config("TEF", total_shares=50)
        _add_buy(fake, "XMAD:TEF", quantity="75", gross_eur="7500")

        resp = c.get("/api/admin/total-shares-reconciliation")
        data = resp.json()
        summary = data["summary"]
        assert summary["total_symbols"] == 3
        assert summary["matched"] == 1
        assert summary["mismatched"] == 1
        assert summary["no_portfolio_data"] == 1
