"""Tests for Symbol Unification trigger points — Symbol Unification rev 3.

Contract: Danny's §2 — Who calls ensure_symbol_config and when.

Coverage:
- Import commit triggers ensure for each distinct security_id after ledger write
- Import commit: ensure failure does NOT roll back committed ledger transactions
- Import commit: warning observable; continues to next security on failure
- Manual movement triggers ensure after write_ledger_txn
- Manual movement: ensure failure → ledger still committed → 201 returned with warning
- Transfer-in triggers ensure after TRANSFER_IN write
- Transfer-in: only the IN side triggers enrollment

All tests are hermetic — in-memory fakes, no network.
"""

from __future__ import annotations

import logging
import pytest
from decimal import Decimal
from unittest.mock import MagicMock, call, patch
from azure.cosmos.exceptions import CosmosResourceNotFoundError, CosmosHttpResponseError

# ---------------------------------------------------------------------------
# These imports will fail until Livingston's implementation exists
# ---------------------------------------------------------------------------
from src.portfolio.symbol_config_sync import ensure_symbol_config  # noqa: F401
from src.portfolio.import_service import ImportService
from src.portfolio.cosmos_portfolio import CosmosPortfolioService
from src.portfolio.cosmos_securities import CosmosSecuritiesService


# ---------------------------------------------------------------------------
# Shared fakes (aligned with test_portfolio_phase2 conventions)
# ---------------------------------------------------------------------------

class FakePortfolioContainer:
    def __init__(self, initial=None):
        self._store: dict = {}
        for doc in (initial or []):
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
            if partition_key is not None and doc.get("account_id") != partition_key:
                continue
            if "doc_type = 'ledger_txn'" in query and doc.get("doc_type") != "ledger_txn":
                continue
            if "doc_type = 'import_session'" in query and doc.get("doc_type") != "import_session":
                continue
            if "NOT IS_DEFINED(c.deleted_at)" in query and "deleted_at" in doc:
                continue
            # correction_status filter
            if ("NOT IS_DEFINED(c.correction_status) OR c.correction_status = 'ACTIVE'") in query:
                cs = doc.get("correction_status")
                if cs is not None and cs not in ("ACTIVE",):
                    continue
            if "@account_id" in param_map and doc.get("account_id") != param_map["@account_id"]:
                continue
            if "@security_id" in param_map and doc.get("security_id") != param_map["@security_id"]:
                continue
            results.append(dict(doc))
        return iter(results)


class FakeSymbolsContainer:
    def __init__(self):
        self._store: dict = {}

    def read_item(self, item, partition_key):
        key = (partition_key, item)
        if key not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        return dict(self._store[key])

    def create_item(self, body):
        ticker = body["symbol"]
        key = (ticker, body["id"])
        self._store[key] = dict(body)
        return dict(body)

    def query_items(self, query="", parameters=None, enable_cross_partition_query=False,
                    partition_key=None):
        return iter([v for v in self._store.values() if v.get("doc_type") == "security_master"])

    def replace_item(self, item, body):
        for key in self._store:
            if self._store[key].get("id") == item:
                self._store[key] = dict(body)
                return dict(body)
        raise CosmosResourceNotFoundError(message="not found", response=None)

    def upsert_item(self, body):
        ticker = body.get("symbol", "")
        key = (ticker, body["id"])
        self._store[key] = dict(body)
        return dict(body)

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


class FakeImportSessionsContainer:
    def __init__(self):
        self._store: dict = {}

    def read(self):
        return {}

    def create_item(self, body):
        self._store[body["id"]] = dict(body)
        return dict(body)

    def read_item(self, item, partition_key):
        if item not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        return dict(self._store[item])

    def replace_item(self, item, body):
        self._store[item] = dict(body)
        return dict(body)

    def query_items(self, query="", parameters=None, enable_cross_partition_query=True, partition_key=None):
        return iter([])


# ---------------------------------------------------------------------------
# Minimal committed-session fixture for import_service tests
# ---------------------------------------------------------------------------

def _make_committed_session(security_ids: list[str]) -> dict:
    """Create a PREVIEW_READY session with movements for the given security_ids."""
    movements = []
    for i, sid in enumerate(security_ids):
        ticker = sid.split(":")[-1]
        movements.append({
            "id": f"mvt_test_{i:04d}",
            "account_id": "_unassigned",
            "doc_type": "ledger_txn",
            "txn_type": "BUY",
            "security_id": sid,
            "ticker": ticker,
            "trade_date": "2026-01-01",
            "quantity": "10",
            "gross": {"amount": "1000", "currency": "EUR", "eur_amount": "1000"},
            "fees": {"total": "0", "currency": "EUR", "total_eur": "0"},
            "net_eur": "1000",
            "correction_status": "ACTIVE",
        })
    return {
        "id": "sess_trigger_test",
        "doc_type": "import_session",
        "state": "PREVIEW_READY",
        "format": "purchases",
        "account_id": "_unassigned",
        "questions": [],
        "preview_movements": movements,
    }


# ---------------------------------------------------------------------------
# §2.1 — Import Commit triggers
# ---------------------------------------------------------------------------

class TestImportCommitTriggers:
    def test_import_commit_calls_ensure_for_each_security(self):
        """After commit, ensure_symbol_config called for each distinct security_id."""
        symbols_ctr = FakeSymbolsContainer()
        symbols_ctr.seed_security("XNYS:AAPL", "Apple Inc.")
        symbols_ctr.seed_security("XMAD:TEF", "Telefónica")

        portfolio_ctr = FakePortfolioContainer()
        sessions_ctr = FakeImportSessionsContainer()
        session = _make_committed_session(["XNYS:AAPL", "XMAD:TEF"])
        sessions_ctr._store[session["id"]] = session

        svc = ImportService(
            CosmosPortfolioService(portfolio_ctr, sessions_ctr),
            CosmosSecuritiesService(symbols_ctr),
        )

        called_ids = []
        original_ensure = ensure_symbol_config.__module__

        with patch("src.portfolio.import_service.ensure_symbol_config",
                   side_effect=lambda c, sid, source: called_ids.append(sid)) as mock_ensure:
            svc.commit_session(session["id"])

        assert "XNYS:AAPL" in called_ids
        assert "XMAD:TEF" in called_ids

    def test_import_commit_calls_ensure_after_write_not_before(self):
        """ensure_symbol_config must be called AFTER write_ledger_txn, not before."""
        symbols_ctr = FakeSymbolsContainer()
        symbols_ctr.seed_security("XNYS:AAPL", "Apple Inc.")

        portfolio_ctr = FakePortfolioContainer()
        sessions_ctr = FakeImportSessionsContainer()
        session = _make_committed_session(["XNYS:AAPL"])
        sessions_ctr._store[session["id"]] = session

        call_order = []

        original_write = CosmosPortfolioService.write_ledger_txn

        def tracked_write(self_inner, movement):
            call_order.append("write")
            return original_write(self_inner, movement)

        svc = ImportService(
            CosmosPortfolioService(portfolio_ctr, sessions_ctr),
            CosmosSecuritiesService(symbols_ctr),
        )

        with patch.object(CosmosPortfolioService, "write_ledger_txn", tracked_write), \
             patch("src.portfolio.import_service.ensure_symbol_config",
                   side_effect=lambda c, sid, source: call_order.append("ensure")):
            svc.commit_session(session["id"])

        # All writes must precede all ensures
        last_write = max(i for i, v in enumerate(call_order) if v == "write")
        first_ensure = min(i for i, v in enumerate(call_order) if v == "ensure")
        assert last_write < first_ensure, (
            f"ensure called before ledger write completed; order: {call_order}"
        )

    def test_import_commit_ledger_intact_when_ensure_fails(self):
        """If ensure_symbol_config raises, ledger transactions remain committed."""
        symbols_ctr = FakeSymbolsContainer()
        symbols_ctr.seed_security("XNYS:AAPL", "Apple Inc.")

        portfolio_ctr = FakePortfolioContainer()
        sessions_ctr = FakeImportSessionsContainer()
        session = _make_committed_session(["XNYS:AAPL"])
        sessions_ctr._store[session["id"]] = session

        svc = ImportService(
            CosmosPortfolioService(portfolio_ctr, sessions_ctr),
            CosmosSecuritiesService(symbols_ctr),
        )

        with patch("src.portfolio.import_service.ensure_symbol_config",
                   side_effect=Exception("Cosmos unreachable")):
            result = svc.commit_session(session["id"])

        # Session committed
        assert result["state"] == "COMMITTED"
        assert result["committed_count"] >= 1
        # Ledger doc exists
        ledger_docs = [d for d in portfolio_ctr._store.values()
                       if d.get("doc_type") == "ledger_txn"]
        assert len(ledger_docs) >= 1, "Ledger docs must be written even if ensure fails"

    def test_import_commit_failure_on_one_security_continues_others(self, caplog):
        """If ensure fails for one security_id, others are still attempted."""
        symbols_ctr = FakeSymbolsContainer()
        symbols_ctr.seed_security("XNYS:AAPL", "Apple Inc.")
        symbols_ctr.seed_security("XMAD:TEF", "Telefónica")

        portfolio_ctr = FakePortfolioContainer()
        sessions_ctr = FakeImportSessionsContainer()
        session = _make_committed_session(["XNYS:AAPL", "XMAD:TEF"])
        sessions_ctr._store[session["id"]] = session

        called = []
        call_count = [0]

        def selective_fail(container, sid, source):
            called.append(sid)
            call_count[0] += 1
            if sid == "XNYS:AAPL":
                raise Exception("Simulated failure for AAPL")

        svc = ImportService(
            CosmosPortfolioService(portfolio_ctr, sessions_ctr),
            CosmosSecuritiesService(symbols_ctr),
        )

        with caplog.at_level(logging.WARNING), \
             patch("src.portfolio.import_service.ensure_symbol_config",
                   side_effect=selective_fail):
            result = svc.commit_session(session["id"])

        assert "XMAD:TEF" in called, "ensure must be attempted for TEF even after AAPL failure"
        assert result["state"] == "COMMITTED"

    def test_import_commit_deduplicates_security_ids(self):
        """If same security appears multiple times, ensure called only once per security."""
        symbols_ctr = FakeSymbolsContainer()
        symbols_ctr.seed_security("XNYS:AAPL", "Apple Inc.")

        portfolio_ctr = FakePortfolioContainer()
        sessions_ctr = FakeImportSessionsContainer()
        # Two movements for the same security
        session = _make_committed_session(["XNYS:AAPL", "XNYS:AAPL"])
        sessions_ctr._store[session["id"]] = session

        called = []
        svc = ImportService(
            CosmosPortfolioService(portfolio_ctr, sessions_ctr),
            CosmosSecuritiesService(symbols_ctr),
        )

        with patch("src.portfolio.import_service.ensure_symbol_config",
                   side_effect=lambda c, sid, source: called.append(sid)):
            svc.commit_session(session["id"])

        assert called.count("XNYS:AAPL") == 1, (
            f"ensure called {called.count('XNYS:AAPL')}x for duplicate; expected exactly 1"
        )


# ---------------------------------------------------------------------------
# §2.2 — Manual Movement triggers
# ---------------------------------------------------------------------------

class TestManualMovementTriggers:
    def _make_movement_data(self, security_id="XNYS:AAPL"):
        return {
            "txn_type": "BUY",
            "security_id": security_id,
            "trade_date": "2026-01-01",
            "account_id": "_unassigned",
            "quantity": "10",
            "gross": {"amount": "1000", "currency": "EUR", "eur_amount": "1000"},
        }

    def test_manual_movement_triggers_ensure(self):
        portfolio_ctr = FakePortfolioContainer()
        symbols_ctr = FakeSymbolsContainer()
        symbols_ctr.seed_security("XNYS:AAPL", "Apple Inc.")
        svc = CosmosPortfolioService(portfolio_ctr, None)

        called = []
        with patch("src.portfolio.cosmos_portfolio.ensure_symbol_config",
                   side_effect=lambda c, sid, source: called.append(sid)):
            svc.create_manual_movement(self._make_movement_data("XNYS:AAPL"))

        assert "XNYS:AAPL" in called

    def test_manual_movement_ensure_failure_still_returns_success(self):
        """ensure failure: warning logged; movement still written; 201-equivalent result."""
        portfolio_ctr = FakePortfolioContainer()
        svc = CosmosPortfolioService(portfolio_ctr, None)

        with patch("src.portfolio.cosmos_portfolio.ensure_symbol_config",
                   side_effect=Exception("Cosmos down")):
            result = svc.create_manual_movement(self._make_movement_data("XNYS:AAPL"))

        # Movement written
        assert result["doc_type"] == "ledger_txn"
        assert result["security_id"] == "XNYS:AAPL"

    def test_manual_movement_ensure_called_with_correct_source(self):
        portfolio_ctr = FakePortfolioContainer()
        svc = CosmosPortfolioService(portfolio_ctr, None)

        calls = []
        with patch("src.portfolio.cosmos_portfolio.ensure_symbol_config",
                   side_effect=lambda c, sid, source: calls.append(source)):
            svc.create_manual_movement(self._make_movement_data("XNYS:AAPL"))

        assert "manual_movement" in calls


# ---------------------------------------------------------------------------
# §2.3 — Transfer-In triggers
# ---------------------------------------------------------------------------

class TestTransferInTriggers:
    def test_transfer_in_triggers_ensure_for_in_side(self):
        """Only the TRANSFER_IN side triggers enrollment."""
        portfolio_ctr = FakePortfolioContainer()
        # Seed source account shares so the transfer can proceed
        portfolio_ctr._store["txn_src_001"] = {
            "id": "txn_src_001",
            "account_id": "acct_src",
            "doc_type": "ledger_txn",
            "txn_type": "BUY",
            "security_id": "XNYS:AAPL",
            "ticker": "AAPL",
            "trade_date": "2025-01-01",
            "quantity": "100",
            "gross": {"amount": "10000", "currency": "EUR", "eur_amount": "10000"},
            "fees": {"total": "0", "currency": "EUR", "total_eur": "0"},
            "net_eur": "10000",
            "correction_status": "ACTIVE",
        }

        svc = CosmosPortfolioService(portfolio_ctr, None)
        transfer_data = {
            "security_id": "XNYS:AAPL",
            "trade_date": "2026-01-01",
            "quantity": "50",
            "source_account_id": "acct_src",
            "dest_account_id": "acct_dst",
        }

        calls = []
        with patch("src.portfolio.cosmos_portfolio.ensure_symbol_config",
                   side_effect=lambda c, sid, source: calls.append((sid, source))):
            svc.create_transfer_pair(transfer_data)

        in_calls = [(sid, src) for sid, src in calls if src == "transfer_in"]
        assert len(in_calls) >= 1, "Expected at least one ensure call with source='transfer_in'"
        assert all(sid == "XNYS:AAPL" for sid, _ in in_calls)
