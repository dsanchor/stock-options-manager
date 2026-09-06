"""Phase 2 regression tests — Movement correction (supersession chain).

Covers:
- POST /api/portfolio/movements/{id}/correct — creates replacement
- Original movement preserved with correction_status=SUPERSEDED
- Replacement is independent (correction_status=ACTIVE, references original)
- Double-correction blocked (already superseded → 409)
- Superseded movements excluded from holdings / totals
- Correction response shape: {"original": {...}, "replacement": {...}}

Route: POST /api/portfolio/movements/{movement_id}/correct
Body:  {account_id, correction_note, trade_date?, quantity?, gross?, fees?, ...}
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


def _seed(fake, mid, txn_type="BUY", quantity="100", gross_eur="18250",
          security_id="XNYS:AAPL", account_id="_unassigned",
          trade_date="2024-01-15", correction_status="ACTIVE",
          commission_eur="7.50"):
    ticker = security_id.split(":")[-1]
    net = str(Decimal(gross_eur) - Decimal(commission_eur))
    doc = {
        "id": mid,
        "doc_type": "ledger_txn",
        "txn_type": txn_type,
        "security_id": security_id,
        "ticker": ticker,
        "trade_date": trade_date,
        "quantity": quantity,
        "gross": {"amount": gross_eur, "currency": "EUR", "eur_amount": gross_eur},
        "fees": {"total": commission_eur, "currency": "EUR", "total_eur": commission_eur},
        "net": {"amount": net, "currency": "EUR", "eur_amount": net},
        "account_id": account_id,
        "correction_status": correction_status,
        "import_source": "manual",
        "created_at": "2026-09-06T10:00:00Z",
        "warnings": [],
    }
    fake.portfolio_container._store[mid] = doc
    return doc


def _correct(c, mid, account_id="_unassigned", note="fixing quantity",
             quantity=None, gross_eur=None, commission_eur=None, trade_date=None):
    body = {"account_id": account_id, "correction_note": note}
    if quantity is not None:
        body["quantity"] = quantity
    if gross_eur is not None:
        body["gross"] = {"amount": gross_eur, "currency": "EUR", "eur_amount": gross_eur}
    if commission_eur is not None:
        body["fees"] = {"total": commission_eur, "currency": "EUR", "total_eur": commission_eur}
    if trade_date is not None:
        body["trade_date"] = trade_date
    return c.post(f"/api/portfolio/movements/{mid}/correct", json=body)


# ===========================================================================
# Correction — basic response shape
# ===========================================================================

class TestCorrectionResponseShape:
    def test_correct_200(self, client):
        c, fake = client
        _seed(fake, "mvt_corr_001")
        resp = _correct(c, "mvt_corr_001", quantity="95")
        assert resp.status_code == 200

    def test_response_has_original_and_replacement(self, client):
        c, fake = client
        _seed(fake, "mvt_corr_002")
        resp = _correct(c, "mvt_corr_002", quantity="95")
        assert resp.status_code == 200
        data = resp.json()
        assert "original" in data, "Response must have 'original' key"
        assert "replacement" in data, "Response must have 'replacement' key"

    def test_original_is_preserved_in_response(self, client):
        c, fake = client
        _seed(fake, "mvt_corr_003")
        resp = _correct(c, "mvt_corr_003", quantity="80")
        assert resp.status_code == 200
        orig = resp.json()["original"]
        assert orig["id"] == "mvt_corr_003"
        assert orig["quantity"] == "100"  # old quantity preserved in original

    def test_replacement_has_new_values(self, client):
        c, fake = client
        _seed(fake, "mvt_corr_004")
        resp = _correct(c, "mvt_corr_004", quantity="77")
        assert resp.status_code == 200
        repl = resp.json()["replacement"]
        assert repl["quantity"] == "77"
        assert repl["id"] != "mvt_corr_004"  # different ID

    def test_replacement_is_active(self, client):
        c, fake = client
        _seed(fake, "mvt_corr_005")
        resp = _correct(c, "mvt_corr_005", quantity="90")
        assert resp.status_code == 200
        assert resp.json()["replacement"]["correction_status"] == "ACTIVE"


# ===========================================================================
# Correction — invariants
# ===========================================================================

class TestCorrectionInvariants:
    def test_original_marked_superseded_in_store(self, client):
        c, fake = client
        _seed(fake, "mvt_sup_001")
        _correct(c, "mvt_sup_001", quantity="88")
        stored = fake.portfolio_container._store.get("mvt_sup_001", {})
        assert stored.get("correction_status") == "SUPERSEDED", (
            "Original must be SUPERSEDED in Cosmos after correction"
        )

    def test_original_has_superseded_by_pointer(self, client):
        c, fake = client
        _seed(fake, "mvt_sup_002")
        resp = _correct(c, "mvt_sup_002", quantity="88")
        stored = fake.portfolio_container._store.get("mvt_sup_002", {})
        assert "superseded_by" in stored, (
            "Original must have superseded_by pointer to the replacement ID"
        )
        repl_id = resp.json()["replacement"]["id"]
        assert stored["superseded_by"] == repl_id

    def test_replacement_has_corrects_pointer(self, client):
        c, fake = client
        _seed(fake, "mvt_sup_003")
        resp = _correct(c, "mvt_sup_003", quantity="60")
        repl_id = resp.json()["replacement"]["id"]
        stored_repl = fake.portfolio_container._store.get(repl_id, {})
        assert stored_repl.get("corrects_movement_id") == "mvt_sup_003", (
            "Replacement must have corrects_movement_id pointing to the original"
        )

    def test_correction_note_stored_on_replacement(self, client):
        c, fake = client
        _seed(fake, "mvt_note_001")
        _correct(c, "mvt_note_001", note="Incorrect share count, was 100 not 95")
        replacements = [
            v for k, v in fake.portfolio_container._store.items()
            if v.get("corrects_movement_id") == "mvt_note_001"
        ]
        assert len(replacements) >= 1, "Replacement must be stored in Cosmos"
        repl = replacements[0]
        assert "correction_note" in repl, (
            "correction_note must be stored on the replacement document"
        )

    def test_double_correction_409(self, client):
        c, fake = client
        _seed(fake, "mvt_double_001")
        _correct(c, "mvt_double_001", quantity="90")  # First correction — succeeds
        resp = _correct(c, "mvt_double_001", quantity="80")  # Second — already SUPERSEDED → 409
        assert resp.status_code == 409, (
            "Correcting an already-SUPERSEDED movement must return 409"
        )
        data = resp.json()
        assert data["error"] in ("already_superseded", "conflict", "cannot_correct")

    def test_correct_nonexistent_404(self, client):
        c, _ = client
        resp = _correct(c, "mvt_ghost_001")
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"

    def test_correct_missing_account_id_400(self, client):
        c, fake = client
        _seed(fake, "mvt_noact_001")
        resp = c.post("/api/portfolio/movements/mvt_noact_001/correct",
                      json={"correction_note": "note"})
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

    def test_correct_missing_note_400(self, client):
        c, fake = client
        _seed(fake, "mvt_nonote_001")
        resp = c.post("/api/portfolio/movements/mvt_nonote_001/correct",
                      json={"account_id": "_unassigned"})
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"


# ===========================================================================
# Correction — holdings / holdings exclusion
# ===========================================================================

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
        return iter(results)

    def read_item(self, item=None, partition_key=None, **kw):
        for m in self._movements:
            if m.get("id") == item:
                return dict(m)
        raise CosmosResourceNotFoundError(message="nf", response=None)

    def upsert_item(self, b): self._movements.append(b); return b
    def replace_item(self, i, b): return b


class FakeSymbols:
    def query_items(self, **kw): return iter([])
    def read_item(self, *a, **kw): raise CosmosResourceNotFoundError("nf", None)


def _mvt(mid, sid, txn, qty, gross, acct="_unassigned", commission="0",
         trade_date="2024-01-15", correction_status="ACTIVE"):
    ticker = sid.split(":")[-1]
    net = str(Decimal(gross) - Decimal(commission))
    return {
        "id": mid, "doc_type": "ledger_txn", "txn_type": txn,
        "security_id": sid, "ticker": ticker, "trade_date": trade_date,
        "quantity": str(qty),
        "gross": {"amount": gross, "currency": "EUR", "eur_amount": gross},
        "fees": {"total": commission, "currency": "EUR", "total_eur": commission},
        "net": {"amount": net, "currency": "EUR", "eur_amount": net},
        "account_id": acct, "correction_status": correction_status,
        "warnings": [],
    }


class TestCorrectionHoldingsExclusion:
    def test_superseded_movement_excluded_from_shares(self):
        movements = [
            _mvt("b_orig", "XNYS:AAPL", "BUY", 100, "18250", correction_status="SUPERSEDED"),
            _mvt("b_repl", "XNYS:AAPL", "BUY", 90, "16425"),  # replacement
        ]
        svc = HoldingsService(
            CosmosPortfolioService(FakePortfolioForHoldings(movements), None),
            CosmosSecuritiesService(FakeSymbols()),
        )
        result = svc.compute_holdings()
        aapl = next((h for h in result["holdings"] if h["security_id"] == "XNYS:AAPL"), None)
        assert aapl is not None
        assert Decimal(aapl["total_shares"]) == Decimal("90"), (
            "SUPERSEDED movement must be excluded; only replacement counted"
        )

    def test_superseded_buy_excluded_from_total_purchases(self):
        movements = [
            _mvt("b_sup1", "XNYS:AAPL", "BUY", 100, "18250", correction_status="SUPERSEDED"),
            _mvt("b_act1", "XNYS:AAPL", "BUY", 90, "16425"),
        ]
        svc = HoldingsService(
            CosmosPortfolioService(FakePortfolioForHoldings(movements), None),
            CosmosSecuritiesService(FakeSymbols()),
        )
        result = svc.compute_holdings()
        aapl = next(h for h in result["holdings"] if h["security_id"] == "XNYS:AAPL")
        assert Decimal(aapl["total_purchases_eur"]) == Decimal("16425.00"), (
            "Superseded BUY gross must not appear in total_purchases_eur"
        )

    def test_replacement_increments_correctly(self):
        movements = [
            _mvt("b_s1", "XMAD:SAN", "BUY", 500, "2250", correction_status="SUPERSEDED"),
            _mvt("b_r1", "XMAD:SAN", "BUY", 480, "2160"),
        ]
        svc = HoldingsService(
            CosmosPortfolioService(FakePortfolioForHoldings(movements), None),
            CosmosSecuritiesService(FakeSymbols()),
        )
        result = svc.compute_holdings()
        san = next(h for h in result["holdings"] if h["security_id"] == "XMAD:SAN")
        assert Decimal(san["total_shares"]) == Decimal("480")


# ===========================================================================
# write_ledger_txn — safety guard against restoring voided/superseded movements
# ===========================================================================

class FakePortfolioForWriteGuard:
    """Minimal fake that supports upsert_item and read_item for the write guard tests."""
    def __init__(self, initial_docs=None):
        self._store: dict = {}
        for d in (initial_docs or []):
            self._store[d["id"]] = dict(d)

    def upsert_item(self, body):
        self._store[body["id"]] = dict(body)
        return dict(body)

    def read_item(self, item=None, partition_key=None, **kw):
        key = item
        if key not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        doc = self._store[key]
        if partition_key is not None and doc.get("account_id") != partition_key:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        return dict(doc)

    def read(self): return {}

    def query_items(self, **kw): return iter([])
    def replace_item(self, i, b): return b
    def create_item(self, b): self._store[b["id"]] = b; return b


def _make_svc(initial_docs=None):
    fake_container = FakePortfolioForWriteGuard(initial_docs)
    return CosmosPortfolioService(fake_container, None), fake_container


class TestWriteLedgerTxnSafetyGuard:
    """write_ledger_txn must never silently restore VOIDED/SUPERSEDED movements."""

    def test_new_document_upserted_normally(self):
        svc, container = _make_svc()
        doc = {
            "id": "mvt_new_001", "account_id": "_unassigned",
            "doc_type": "ledger_txn", "txn_type": "BUY",
        }
        result = svc.write_ledger_txn(doc)
        assert result["id"] == "mvt_new_001"
        assert "mvt_new_001" in container._store

    def test_active_document_upserted_normally(self):
        existing = {
            "id": "mvt_active_001", "account_id": "_unassigned",
            "doc_type": "ledger_txn", "txn_type": "BUY",
            "correction_status": "ACTIVE",
        }
        svc, container = _make_svc([existing])
        new_doc = {**existing, "quantity": "200"}
        result = svc.write_ledger_txn(new_doc)
        assert result["id"] == "mvt_active_001"
        assert container._store["mvt_active_001"]["quantity"] == "200"

    def test_voided_document_raises_not_overwritten(self):
        voided = {
            "id": "mvt_voided_001", "account_id": "_unassigned",
            "doc_type": "ledger_txn", "txn_type": "BUY",
            "correction_status": "VOIDED",
        }
        svc, container = _make_svc([voided])
        incoming = {
            "id": "mvt_voided_001", "account_id": "_unassigned",
            "doc_type": "ledger_txn", "txn_type": "BUY",
            # No correction_status — would silently restore if not guarded
        }
        with pytest.raises(CosmosPortfolioService.VoidedMovementError) as exc_info:
            svc.write_ledger_txn(incoming)
        assert exc_info.value.status == "VOIDED"
        assert exc_info.value.movement_id == "mvt_voided_001"
        # Document must remain VOIDED in store
        assert container._store["mvt_voided_001"]["correction_status"] == "VOIDED"

    def test_superseded_document_raises_not_overwritten(self):
        superseded = {
            "id": "mvt_sup_guard_001", "account_id": "_unassigned",
            "doc_type": "ledger_txn", "txn_type": "SELL",
            "correction_status": "SUPERSEDED",
        }
        svc, container = _make_svc([superseded])
        incoming = {
            "id": "mvt_sup_guard_001", "account_id": "_unassigned",
            "doc_type": "ledger_txn", "txn_type": "SELL",
        }
        with pytest.raises(CosmosPortfolioService.VoidedMovementError) as exc_info:
            svc.write_ledger_txn(incoming)
        assert exc_info.value.status == "SUPERSEDED"
        # Document must remain SUPERSEDED in store
        assert container._store["mvt_sup_guard_001"]["correction_status"] == "SUPERSEDED"

    def test_document_without_account_id_skips_guard(self):
        """If account_id is missing, the guard cannot query Cosmos — proceed with upsert."""
        svc, container = _make_svc()
        doc = {"id": "mvt_no_acct_001", "doc_type": "ledger_txn"}
        # Should not raise — guard skipped when account_id is absent
        result = svc.write_ledger_txn(doc)
        assert result["id"] == "mvt_no_acct_001"
