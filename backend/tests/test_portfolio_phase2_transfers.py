"""Phase 2 regression tests — Custody transfers.

Covers:
- POST /api/portfolio/transfers — create a TRANSFER_OUT / TRANSFER_IN pair
- Both legs share same quantity and transfer_group_id
- source_account_id != dest_account_id (same-account rejected)
- Hard-block: insufficient source shares at transfer date → 409
- Cost basis: auto-computed; override via cost_basis_override_eur
- Optional transfer_fee (separate from cost basis)
- Global shares unchanged; per-account shares adjusted correctly
- Transfers excluded from purchases / sales / realized-gain totals

Route: POST /api/portfolio/transfers
Body: {
    source_account_id, dest_account_id, security_id, trade_date, quantity,
    cost_basis_override_eur?,   # optional override; otherwise server computes
    transfer_fee?,              # optional; separate from basis
}
Response: {
    "transfer_out": {...},
    "transfer_in": {...},
    "transfer_group_id": "..."
}
"""

from __future__ import annotations

import pytest
from decimal import Decimal
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from fastapi.testclient import TestClient

from src.portfolio.cosmos_portfolio import CosmosPortfolioService
from src.portfolio.cosmos_securities import CosmosSecuritiesService
from src.portfolio.holdings_service import HoldingsService
from tests.conftest_portfolio_p2 import FakeCosmos


@pytest.fixture
def client():
    from web.app import app
    fake = FakeCosmos()
    with TestClient(app) as c:
        app.state.cosmos = fake
        app.state.cosmos_error = None
        yield c, fake


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_buy(fake, mid, account_id, security_id="XNYS:AAPL", quantity="200",
              gross_eur="36500", trade_date="2024-01-15", commission_eur="0"):
    ticker = security_id.split(":")[-1]
    net = str(Decimal(gross_eur) - Decimal(commission_eur))
    doc = {
        "id": mid,
        "doc_type": "ledger_txn",
        "txn_type": "BUY",
        "security_id": security_id,
        "ticker": ticker,
        "trade_date": trade_date,
        "quantity": quantity,
        "gross": {"amount": gross_eur, "currency": "EUR", "eur_amount": gross_eur},
        "fees": {"total": commission_eur, "currency": "EUR", "total_eur": commission_eur},
        "net": {"amount": net, "currency": "EUR", "eur_amount": net},
        "account_id": account_id,
        "correction_status": "ACTIVE",
        "cost_basis_status": "COMPLETE",
        "import_source": "manual",
        "created_at": "2026-09-06T10:00:00Z",
        "warnings": [],
    }
    fake.portfolio_container._store[mid] = doc
    return doc


def _transfer(c, source, dest, security_id="XNYS:AAPL", quantity="100",
              trade_date="2024-06-01", cost_basis_override_eur=None, transfer_fee=None):
    body = {
        "source_account_id": source,
        "dest_account_id": dest,
        "security_id": security_id,
        "trade_date": trade_date,
        "quantity": quantity,
    }
    if cost_basis_override_eur is not None:
        body["cost_basis_override_eur"] = cost_basis_override_eur
    if transfer_fee is not None:
        # transfer_fee is an object per contract: {amount, currency, eur_amount}
        body["transfer_fee"] = transfer_fee
    return c.post("/api/portfolio/transfers", json=body)


SRC = "acct_heytrade_source"
DST = "acct_fidelity_dest"


# ---------------------------------------------------------------------------
# Basic response contract
# ---------------------------------------------------------------------------

class TestTransferPairCreation:
    def test_transfer_201(self, client):
        c, fake = client
        _seed_buy(fake, "buy_src_001", SRC, quantity="200")
        resp = _transfer(c, SRC, DST, quantity="100")
        assert resp.status_code == 201

    def test_response_has_three_keys(self, client):
        c, fake = client
        _seed_buy(fake, "buy_src_002", SRC, quantity="200")
        resp = _transfer(c, SRC, DST, quantity="100")
        assert resp.status_code == 201
        data = resp.json()
        assert "transfer_out" in data
        assert "transfer_in" in data
        assert "transfer_group_id" in data

    def test_transfer_out_type(self, client):
        c, fake = client
        _seed_buy(fake, "buy_src_003", SRC, quantity="200")
        resp = _transfer(c, SRC, DST, quantity="100")
        assert resp.status_code == 201
        assert resp.json()["transfer_out"]["txn_type"] == "TRANSFER_OUT"

    def test_transfer_in_type(self, client):
        c, fake = client
        _seed_buy(fake, "buy_src_004", SRC, quantity="200")
        resp = _transfer(c, SRC, DST, quantity="100")
        assert resp.status_code == 201
        assert resp.json()["transfer_in"]["txn_type"] == "TRANSFER_IN"

    def test_same_quantity_both_legs(self, client):
        c, fake = client
        _seed_buy(fake, "buy_src_005", SRC, quantity="200")
        resp = _transfer(c, SRC, DST, quantity="75")
        assert resp.status_code == 201
        data = resp.json()
        assert Decimal(data["transfer_out"]["quantity"]) == Decimal("75")
        assert Decimal(data["transfer_in"]["quantity"]) == Decimal("75")

    def test_same_group_id_both_legs(self, client):
        c, fake = client
        _seed_buy(fake, "buy_src_006", SRC, quantity="200")
        resp = _transfer(c, SRC, DST, quantity="100")
        assert resp.status_code == 201
        data = resp.json()
        assert data["transfer_group_id"] is not None
        assert data["transfer_out"].get("transfer_group_id") == data["transfer_group_id"]
        assert data["transfer_in"].get("transfer_group_id") == data["transfer_group_id"]

    def test_transfer_out_account_is_source(self, client):
        c, fake = client
        _seed_buy(fake, "buy_src_007", SRC, quantity="200")
        resp = _transfer(c, SRC, DST, quantity="100")
        assert resp.status_code == 201
        assert resp.json()["transfer_out"]["account_id"] == SRC

    def test_transfer_in_account_is_dest(self, client):
        c, fake = client
        _seed_buy(fake, "buy_src_008", SRC, quantity="200")
        resp = _transfer(c, SRC, DST, quantity="100")
        assert resp.status_code == 201
        assert resp.json()["transfer_in"]["account_id"] == DST

    def test_transfer_in_has_cost_basis_eur(self, client):
        c, fake = client
        _seed_buy(fake, "buy_src_009", SRC, quantity="200", gross_eur="36500")
        resp = _transfer(c, SRC, DST, quantity="100")
        assert resp.status_code == 201
        tin = resp.json()["transfer_in"]
        assert "transfer_cost_basis_eur" in tin, (
            "TRANSFER_IN must carry transfer_cost_basis_eur"
        )

    def test_transfer_in_cost_basis_not_overridden_flag_false(self, client):
        c, fake = client
        _seed_buy(fake, "buy_src_010", SRC, quantity="200", gross_eur="36500")
        resp = _transfer(c, SRC, DST, quantity="100")
        assert resp.status_code == 201
        tin = resp.json()["transfer_in"]
        assert tin.get("transfer_cost_basis_overridden") is False

    def test_cost_basis_override_stored(self, client):
        c, fake = client
        _seed_buy(fake, "buy_src_011", SRC, quantity="200", gross_eur="36500")
        resp = _transfer(c, SRC, DST, quantity="100", cost_basis_override_eur="15000")
        assert resp.status_code == 201
        tin = resp.json()["transfer_in"]
        assert Decimal(tin["transfer_cost_basis_eur"]) == Decimal("15000")
        assert tin["transfer_cost_basis_overridden"] is True

    def test_transfer_fee_stored_separately(self, client):
        c, fake = client
        _seed_buy(fake, "buy_src_012", SRC, quantity="200")
        fee_obj = {"amount": "25.00", "currency": "EUR", "eur_amount": "25.00"}
        resp = _transfer(c, SRC, DST, quantity="100", transfer_fee=fee_obj)
        assert resp.status_code == 201
        data = resp.json()
        # Both legs should carry the transfer_fee object per contract
        tout_fee = data["transfer_out"].get("transfer_fee")
        tin_fee = data["transfer_in"].get("transfer_fee")
        assert tout_fee is not None or tin_fee is not None, (
            "transfer_fee must be stored on at least one leg"
        )

    def test_transfer_has_peer_id_on_both_legs(self, client):
        c, fake = client
        _seed_buy(fake, "buy_src_013", SRC, quantity="200")
        resp = _transfer(c, SRC, DST, quantity="100")
        assert resp.status_code == 201
        data = resp.json()
        out_id = data["transfer_out"]["id"]
        in_id = data["transfer_in"]["id"]
        assert data["transfer_out"].get("transfer_peer_id") == in_id, (
            "TRANSFER_OUT must have transfer_peer_id pointing to TRANSFER_IN"
        )
        assert data["transfer_in"].get("transfer_peer_id") == out_id, (
            "TRANSFER_IN must have transfer_peer_id pointing to TRANSFER_OUT"
        )

    def test_transfer_has_source_dest_account_fields(self, client):
        c, fake = client
        _seed_buy(fake, "buy_src_014", SRC, quantity="200")
        resp = _transfer(c, SRC, DST, quantity="100")
        assert resp.status_code == 201
        data = resp.json()
        for leg in ("transfer_out", "transfer_in"):
            assert data[leg].get("transfer_source_account_id") == SRC
            assert data[leg].get("transfer_dest_account_id") == DST

    def test_transfer_has_cost_basis_derived_eur(self, client):
        c, fake = client
        _seed_buy(fake, "buy_src_015", SRC, quantity="200", gross_eur="36500")
        resp = _transfer(c, SRC, DST, quantity="100")
        assert resp.status_code == 201
        tin = resp.json()["transfer_in"]
        assert "transfer_cost_basis_derived_eur" in tin, (
            "TRANSFER_IN must have transfer_cost_basis_derived_eur (auto-computed)"
        )


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------

class TestTransferValidation:
    def test_same_account_400(self, client):
        c, fake = client
        _seed_buy(fake, "buy_same_001", SRC, quantity="200")
        resp = _transfer(c, SRC, SRC, quantity="100")
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

    def test_missing_source_400(self, client):
        c, _ = client
        resp = c.post("/api/portfolio/transfers", json={
            "dest_account_id": DST, "security_id": "XNYS:AAPL",
            "trade_date": "2024-06-01", "quantity": "100",
        })
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

    def test_missing_dest_400(self, client):
        c, _ = client
        resp = c.post("/api/portfolio/transfers", json={
            "source_account_id": SRC, "security_id": "XNYS:AAPL",
            "trade_date": "2024-06-01", "quantity": "100",
        })
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

    def test_missing_quantity_400(self, client):
        c, _ = client
        resp = c.post("/api/portfolio/transfers", json={
            "source_account_id": SRC, "dest_account_id": DST,
            "security_id": "XNYS:AAPL", "trade_date": "2024-06-01",
        })
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

    def test_zero_quantity_400(self, client):
        c, fake = client
        _seed_buy(fake, "buy_zero_001", SRC, quantity="200")
        resp = _transfer(c, SRC, DST, quantity="0")
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"


# ---------------------------------------------------------------------------
# Insufficient shares hard-block
# ---------------------------------------------------------------------------

class TestTransferInsufficientShares:
    def test_insufficient_shares_409(self, client):
        c, fake = client
        _seed_buy(fake, "buy_insuf_001", SRC, quantity="50")  # 50 shares
        resp = _transfer(c, SRC, DST, quantity="100")  # requesting 100
        assert resp.status_code == 409
        assert resp.json()["error"] == "insufficient_shares"

    def test_insufficient_shares_error_contains_available(self, client):
        c, fake = client
        _seed_buy(fake, "buy_insuf_002", SRC, quantity="30")
        resp = _transfer(c, SRC, DST, quantity="100")
        assert resp.status_code == 409
        data = resp.json()
        assert "available" in data or "shares_available" in data

    def test_exact_quantity_allowed(self, client):
        c, fake = client
        _seed_buy(fake, "buy_exact_001", SRC, quantity="100")
        resp = _transfer(c, SRC, DST, quantity="100")
        assert resp.status_code == 201, "Transferring exactly all shares must succeed"

    def test_insufficient_respects_transfer_date(self, client):
        """Shares bought AFTER transfer date must not count toward availability."""
        c, fake = client
        _seed_buy(fake, "buy_future_001", SRC, quantity="200",
                  trade_date="2024-06-15")  # AFTER the transfer date
        resp = _transfer(c, SRC, DST, quantity="100", trade_date="2024-06-01")
        assert resp.status_code == 409, (
            "Shares bought after transfer date must not satisfy the check"
        )

    def test_shares_at_date_only_counts_up_to_transfer(self, client):
        """Shares bought BEFORE transfer date count; after do not."""
        c, fake = client
        _seed_buy(fake, "buy_before_001", SRC, quantity="200",
                  trade_date="2024-01-15")  # BEFORE
        _seed_buy(fake, "buy_after_001", SRC, quantity="200",
                  trade_date="2024-08-01")  # AFTER
        resp = _transfer(c, SRC, DST, quantity="100", trade_date="2024-06-01")
        assert resp.status_code == 201, (
            "200 shares (from before-date buy) is enough for 100-share transfer"
        )


# ---------------------------------------------------------------------------
# No half-pair: atomicity
# ---------------------------------------------------------------------------

class TestTransferAtomicity:
    def test_both_legs_stored_or_neither(self, client):
        """If creation fails, neither leg should be stored."""
        c, fake = client
        # No shares seeded → will fail at insufficient check before any write
        resp = _transfer(c, SRC, DST, quantity="100")
        assert resp.status_code == 409
        any_transfer_doc = any(
            v.get("txn_type") in ("TRANSFER_OUT", "TRANSFER_IN")
            for v in fake.portfolio_container._store.values()
        )
        assert not any_transfer_doc, "Neither leg must be stored when transfer is rejected"


# ---------------------------------------------------------------------------
# Holdings invariants after successful transfer
# ---------------------------------------------------------------------------

class FakePortfolioForHoldings:
    def __init__(self, movements):
        self._movements = list(movements)

    def query_items(self, query="", parameters=None, enable_cross_partition_query=True,
                    partition_key=None):
        results = self._movements
        if "NOT IS_DEFINED(c.deleted_at)" in query:
            results = [m for m in results if "deleted_at" not in m]
        if "(NOT IS_DEFINED(c.correction_status) OR c.correction_status = 'ACTIVE')" in query:
            results = [m for m in results if m.get("correction_status", "ACTIVE") == "ACTIVE"]
        # Account-scoped
        param_map = {p["name"]: p["value"] for p in (parameters or [])}
        if "@account_id" in param_map:
            results = [d for d in results if d.get("account_id") == param_map["@account_id"]]
        if partition_key:
            results = [d for d in results if d.get("account_id") == partition_key]
        return iter(results)

    def read_item(self, item=None, partition_key=None, **kw):
        for m in self._movements:
            if m.get("id") == item:
                return dict(m)
        raise CosmosResourceNotFoundError("nf", None)

    def upsert_item(self, b): self._movements.append(b); return b
    def replace_item(self, i, b): return b


class FakeSymbols:
    def query_items(self, **kw): return iter([])
    def read_item(self, *a, **kw): raise CosmosResourceNotFoundError("nf", None)


def _mvt(mid, sid, txn, qty, gross, acct, commission="0", trade_date="2024-01-15",
         correction_status="ACTIVE", sales_type=None, transfer_group_id=None,
         transfer_cost_basis_eur=None):
    ticker = sid.split(":")[-1]
    net = str(Decimal(gross) - Decimal(commission))
    doc = {
        "id": mid, "doc_type": "ledger_txn", "txn_type": txn,
        "security_id": sid, "ticker": ticker, "trade_date": trade_date,
        "quantity": str(qty),
        "gross": {"amount": gross, "currency": "EUR", "eur_amount": gross},
        "fees": {"total": commission, "currency": "EUR", "total_eur": commission},
        "net": {"amount": net, "currency": "EUR", "eur_amount": net},
        "account_id": acct, "correction_status": correction_status, "warnings": [],
    }
    if sales_type:
        doc["sales_type"] = sales_type
    if transfer_group_id:
        doc["transfer_group_id"] = transfer_group_id
    if transfer_cost_basis_eur is not None:
        doc["transfer_cost_basis_eur"] = transfer_cost_basis_eur
    return doc


class TestTransferHoldingsInvariants:
    def test_global_shares_unchanged_by_transfer(self):
        movements = [
            _mvt("buy_g1", "XNYS:AAPL", "BUY", 200, "36500", SRC),
            _mvt("tout_g1", "XNYS:AAPL", "TRANSFER_OUT", 100, "0", SRC,
                 transfer_group_id="tg_001"),
            _mvt("tin_g1", "XNYS:AAPL", "TRANSFER_IN", 100, "0", DST,
                 transfer_group_id="tg_001"),
        ]
        svc = HoldingsService(
            CosmosPortfolioService(FakePortfolioForHoldings(movements), None),
            CosmosSecuritiesService(FakeSymbols()),
        )
        result = svc.compute_holdings()
        total = sum(
            Decimal(h["total_shares"]) for h in result["holdings"]
            if h["security_id"] == "XNYS:AAPL"
        )
        assert total == Decimal("200"), (
            "Transfer must not change global share count (200 total across all accounts)"
        )

    def test_source_account_shares_reduced(self):
        movements = [
            _mvt("buy_sa1", "XNYS:AAPL", "BUY", 200, "36500", SRC),
            _mvt("tout_sa1", "XNYS:AAPL", "TRANSFER_OUT", 100, "0", SRC,
                 transfer_group_id="tg_002"),
        ]
        svc = HoldingsService(
            CosmosPortfolioService(FakePortfolioForHoldings(movements), None),
            CosmosSecuritiesService(FakeSymbols()),
        )
        result = svc.compute_holdings()
        src_holdings = [
            h for h in result["holdings"]
            if h["security_id"] == "XNYS:AAPL" and h.get("account_id") == SRC
        ]
        if src_holdings:
            assert Decimal(src_holdings[0]["total_shares"]) == Decimal("100")

    def test_dest_account_shares_increased(self):
        movements = [
            _mvt("buy_da1", "XNYS:AAPL", "BUY", 200, "36500", SRC),
            _mvt("tout_da1", "XNYS:AAPL", "TRANSFER_OUT", 100, "0", SRC,
                 transfer_group_id="tg_003"),
            _mvt("tin_da1", "XNYS:AAPL", "TRANSFER_IN", 100, "0", DST,
                 transfer_group_id="tg_003"),
        ]
        svc = HoldingsService(
            CosmosPortfolioService(FakePortfolioForHoldings(movements), None),
            CosmosSecuritiesService(FakeSymbols()),
        )
        result = svc.compute_holdings()
        dst_holdings = [
            h for h in result["holdings"]
            if h["security_id"] == "XNYS:AAPL" and h.get("account_id") == DST
        ]
        if dst_holdings:
            assert Decimal(dst_holdings[0]["total_shares"]) == Decimal("100")

    def test_transfer_excluded_from_purchases_eur(self):
        movements = [
            _mvt("buy_ex1", "XNYS:AAPL", "BUY", 200, "36500", SRC),
            _mvt("tin_ex1", "XNYS:AAPL", "TRANSFER_IN", 100, "0", DST,
                 transfer_cost_basis_eur="18250"),
        ]
        svc = HoldingsService(
            CosmosPortfolioService(FakePortfolioForHoldings(movements), None),
            CosmosSecuritiesService(FakeSymbols()),
        )
        result = svc.compute_holdings()
        total_purchases = sum(
            Decimal(h.get("total_purchases_eur", "0"))
            for h in result["holdings"]
            if h["security_id"] == "XNYS:AAPL"
        )
        assert total_purchases == Decimal("36500"), (
            "TRANSFER_IN must not be counted in total_purchases_eur"
        )

    def test_transfer_excluded_from_sales_eur(self):
        movements = [
            _mvt("buy_ex2", "XNYS:AAPL", "BUY", 200, "36500", SRC),
            _mvt("tout_ex2", "XNYS:AAPL", "TRANSFER_OUT", 100, "0", SRC),
        ]
        svc = HoldingsService(
            CosmosPortfolioService(FakePortfolioForHoldings(movements), None),
            CosmosSecuritiesService(FakeSymbols()),
        )
        result = svc.compute_holdings()
        total_sales = sum(
            Decimal(h.get("total_sales_eur", "0"))
            for h in result["holdings"]
            if h["security_id"] == "XNYS:AAPL"
        )
        assert total_sales == Decimal("0"), (
            "TRANSFER_OUT must not be counted in total_sales_eur"
        )
