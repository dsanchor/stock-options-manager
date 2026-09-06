"""Phase 2 regression tests — Movement reassignment.

Covers:
- POST /api/portfolio/movements/{id}/reassign — individual reassignment
- POST /api/portfolio/movements/batch-reassign — batch reassignment
- Account scoping: source_account_id required
- Response contracts for both
- Authoritative override: batch reason is optional; omitted/empty/whitespace all default to
  the internal audit string "Batch account reassignment" and are stored verbatim on the doc.
  Individual reassignment still requires a non-blank reason (400 otherwise).
- DEFECT TEST: batch-reassign is NOT atomic (skips failures) — pending fix

No preview endpoint exists in the current implementation.
"""

from __future__ import annotations

import pytest
from decimal import Decimal
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from fastapi.testclient import TestClient

from tests.conftest_portfolio_p2 import FakeCosmos


@pytest.fixture
def client():
    from web.app import app
    fake = FakeCosmos()
    with TestClient(app) as c:
        app.state.cosmos = fake
        app.state.cosmos_error = None
        yield c, fake


def _seed(fake, mid, account_id="_unassigned", security_id="XNYS:AAPL",
          txn_type="BUY", quantity="100", gross_eur="18250",
          trade_date="2024-01-15", correction_status="ACTIVE"):
    ticker = security_id.split(":")[-1]
    doc = {
        "id": mid,
        "doc_type": "ledger_txn",
        "txn_type": txn_type,
        "security_id": security_id,
        "ticker": ticker,
        "trade_date": trade_date,
        "quantity": quantity,
        "gross": {"amount": gross_eur, "currency": "EUR", "eur_amount": gross_eur},
        "fees": {"total": "0", "currency": "EUR", "total_eur": "0"},
        "net": {"amount": gross_eur, "currency": "EUR", "eur_amount": gross_eur},
        "account_id": account_id,
        "correction_status": correction_status,
        "import_source": "manual",
        "created_at": "2026-09-06T10:00:00Z",
        "warnings": [],
    }
    fake.portfolio_container._store[mid] = doc
    return doc


SRC_ACCT = "_unassigned"
DST_ACCT = "acct_heytrade_main"


# ===========================================================================
# Individual reassignment
# ===========================================================================

class TestIndividualReassignment:
    def test_reassign_200(self, client):
        c, fake = client
        _seed(fake, "mvt_rea_001", account_id=SRC_ACCT)
        resp = c.post("/api/portfolio/movements/mvt_rea_001/reassign", json={
            "source_account_id": SRC_ACCT,
            "dest_account_id": DST_ACCT,
            "reason": "imported to wrong account",
        })
        assert resp.status_code == 200

    def test_reassign_response_has_original_id(self, client):
        c, fake = client
        _seed(fake, "mvt_rea_002", account_id=SRC_ACCT)
        resp = c.post("/api/portfolio/movements/mvt_rea_002/reassign", json={
            "source_account_id": SRC_ACCT,
            "dest_account_id": DST_ACCT,
            "reason": "wrong account",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "original_id" in data or "new_id" in data, (
            "Response must contain original_id or new_id"
        )

    def test_reassign_response_has_dest_account(self, client):
        c, fake = client
        _seed(fake, "mvt_rea_003", account_id=SRC_ACCT)
        resp = c.post("/api/portfolio/movements/mvt_rea_003/reassign", json={
            "source_account_id": SRC_ACCT,
            "dest_account_id": DST_ACCT,
            "reason": "wrong account",
        })
        assert resp.status_code == 200
        assert resp.json().get("dest_account_id") == DST_ACCT

    def test_reassign_movement_account_updated(self, client):
        c, fake = client
        _seed(fake, "mvt_rea_004", account_id=SRC_ACCT)
        c.post("/api/portfolio/movements/mvt_rea_004/reassign", json={
            "source_account_id": SRC_ACCT,
            "dest_account_id": DST_ACCT,
            "reason": "moved",
        })
        # The doc should now be in the new account (new ID or same ID, different account_id)
        new_docs = [
            v for v in fake.portfolio_container._store.values()
            if v.get("account_id") == DST_ACCT and v.get("security_id") == "XNYS:AAPL"
        ]
        assert len(new_docs) >= 1, "Movement must be stored under dest_account_id after reassign"

    def test_reassign_missing_source_400(self, client):
        c, fake = client
        _seed(fake, "mvt_rea_nosrc", account_id=SRC_ACCT)
        resp = c.post("/api/portfolio/movements/mvt_rea_nosrc/reassign", json={
            "dest_account_id": DST_ACCT,
            "reason": "test",
        })
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

    def test_reassign_missing_dest_400(self, client):
        c, fake = client
        _seed(fake, "mvt_rea_nodst", account_id=SRC_ACCT)
        resp = c.post("/api/portfolio/movements/mvt_rea_nodst/reassign", json={
            "source_account_id": SRC_ACCT,
            "reason": "test",
        })
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

    def test_reassign_missing_reason_400(self, client):
        c, fake = client
        _seed(fake, "mvt_rea_noreason", account_id=SRC_ACCT)
        resp = c.post("/api/portfolio/movements/mvt_rea_noreason/reassign", json={
            "source_account_id": SRC_ACCT,
            "dest_account_id": DST_ACCT,
        })
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

    def test_reassign_nonexistent_movement_404(self, client):
        c, _ = client
        resp = c.post("/api/portfolio/movements/mvt_ghost/reassign", json={
            "source_account_id": SRC_ACCT,
            "dest_account_id": DST_ACCT,
            "reason": "test",
        })
        assert resp.status_code in (404, 400)

    def test_reassign_wrong_source_account_404(self, client):
        c, fake = client
        _seed(fake, "mvt_rea_wsrc", account_id="acct_other")
        resp = c.post("/api/portfolio/movements/mvt_rea_wsrc/reassign", json={
            "source_account_id": SRC_ACCT,  # wrong source
            "dest_account_id": DST_ACCT,
            "reason": "wrong src",
        })
        assert resp.status_code in (404, 400), (
            "Movement not in source account must be rejected"
        )

    def test_reassign_same_account_409(self, client):
        """Same source and destination raises ValueError('same_account') → 409."""
        c, fake = client
        _seed(fake, "mvt_rea_same", account_id=SRC_ACCT)
        resp = c.post("/api/portfolio/movements/mvt_rea_same/reassign", json={
            "source_account_id": SRC_ACCT,
            "dest_account_id": SRC_ACCT,
            "reason": "no-op",
        })
        assert resp.status_code == 409
        assert resp.json()["error"] == "same_account"


# ===========================================================================
# Batch reassignment
# ===========================================================================

class TestBatchReassignment:
    def test_batch_reassign_200(self, client):
        c, fake = client
        for i in range(3):
            _seed(fake, f"mvt_batch_{i:02d}", account_id=SRC_ACCT,
                  security_id="XNYS:AAPL", trade_date="2024-03-01")
        resp = c.post("/api/portfolio/movements/batch-reassign", json={
            "source_account_id": SRC_ACCT,
            "dest_account_id": DST_ACCT,
            "reason": "batch move",
        })
        assert resp.status_code == 200

    def test_batch_response_has_reassigned_count(self, client):
        c, fake = client
        for i in range(3):
            _seed(fake, f"mvt_bc_{i:02d}", account_id=SRC_ACCT,
                  security_id="XNYS:AAPL", trade_date="2024-03-01")
        resp = c.post("/api/portfolio/movements/batch-reassign", json={
            "source_account_id": SRC_ACCT,
            "dest_account_id": DST_ACCT,
            "reason": "batch",
        })
        assert resp.status_code == 200
        assert "reassigned_count" in resp.json()

    def test_batch_response_has_skipped_count(self, client):
        c, fake = client
        _seed(fake, "mvt_bsk_001", account_id=SRC_ACCT)
        resp = c.post("/api/portfolio/movements/batch-reassign", json={
            "source_account_id": SRC_ACCT,
            "dest_account_id": DST_ACCT,
            "reason": "batch",
        })
        assert resp.status_code == 200
        assert "skipped_count" in resp.json()

    def test_batch_filters_by_security_id(self, client):
        c, fake = client
        _seed(fake, "mvt_bfilt_aapl", account_id=SRC_ACCT, security_id="XNYS:AAPL")
        _seed(fake, "mvt_bfilt_san", account_id=SRC_ACCT, security_id="XMAD:SAN")
        resp = c.post("/api/portfolio/movements/batch-reassign", json={
            "source_account_id": SRC_ACCT,
            "dest_account_id": DST_ACCT,
            "security_id": "XNYS:AAPL",  # filter to AAPL only
            "reason": "filtered batch",
        })
        assert resp.status_code == 200
        # AAPL moved; SAN stays in unassigned
        san_still_here = any(
            v.get("account_id") == SRC_ACCT and v.get("security_id") == "XMAD:SAN"
            for v in fake.portfolio_container._store.values()
        )
        assert san_still_here, "SAN must NOT be moved when security_id filter is AAPL"

    def test_batch_filters_by_date_range(self, client):
        c, fake = client
        _seed(fake, "mvt_bdate_jan", account_id=SRC_ACCT, trade_date="2024-01-15")
        _seed(fake, "mvt_bdate_sep", account_id=SRC_ACCT, trade_date="2024-09-01")
        resp = c.post("/api/portfolio/movements/batch-reassign", json={
            "source_account_id": SRC_ACCT,
            "dest_account_id": DST_ACCT,
            "date_from": "2024-06-01",
            "date_to": "2024-12-31",
            "reason": "date-filtered batch",
        })
        assert resp.status_code == 200
        # Only September movement should move
        jan_still_src = any(
            v.get("account_id") == SRC_ACCT and v.get("trade_date") == "2024-01-15"
            for v in fake.portfolio_container._store.values()
        )
        assert jan_still_src, "January movement must NOT be reassigned (outside date_from/date_to)"

    def test_batch_empty_source_200(self, client):
        c, _ = client
        resp = c.post("/api/portfolio/movements/batch-reassign", json={
            "source_account_id": "acct_empty",
            "dest_account_id": DST_ACCT,
            "reason": "empty batch",
        })
        assert resp.status_code == 200
        assert resp.json()["reassigned_count"] == 0

    def test_batch_response_has_ids_list(self, client):
        """Contract specifies response has 'ids' list of new movement IDs."""
        c, fake = client
        for i in range(2):
            _seed(fake, f"mvt_bids_{i:02d}", account_id=SRC_ACCT,
                  security_id="XNYS:AAPL", trade_date="2024-03-01")
        resp = c.post("/api/portfolio/movements/batch-reassign", json={
            "source_account_id": SRC_ACCT,
            "dest_account_id": DST_ACCT,
            "reason": "batch",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "ids" in data, "Batch reassign response must include 'ids' list of new movement IDs"
        assert isinstance(data["ids"], list)
        c, _ = client
        resp = c.post("/api/portfolio/movements/batch-reassign", json={
            "dest_account_id": DST_ACCT,
            "reason": "test",
        })
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

    def test_batch_missing_dest_400(self, client):
        c, _ = client
        resp = c.post("/api/portfolio/movements/batch-reassign", json={
            "source_account_id": SRC_ACCT,
            "reason": "test",
        })
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

_BATCH_DEFAULT_REASON = "Batch account reassignment"


def _find_new_doc(fake, dest_acct, orig_id):
    """Return the reassigned doc in dest_acct that points at orig_id."""
    return next(
        (
            v for v in fake.portfolio_container._store.values()
            if v.get("account_id") == dest_acct
            and v.get("reassigned_from", {}).get("movement_id") == orig_id
        ),
        None,
    )


class TestBatchReasonOptional:
    """Authoritative override: batch reason is optional; standard audit reason stored."""

    def test_batch_missing_reason_returns_200(self, client):
        """Omitting reason key entirely must not raise a 400."""
        c, fake = client
        _seed(fake, "mvt_noreason_01", account_id=SRC_ACCT)
        resp = c.post("/api/portfolio/movements/batch-reassign", json={
            "source_account_id": SRC_ACCT,
            "dest_account_id": DST_ACCT,
        })
        assert resp.status_code == 200

    def test_batch_missing_reason_stores_default_audit_reason(self, client):
        """When reason is absent the server records the default audit reason."""
        c, fake = client
        _seed(fake, "mvt_noreason_02", account_id=SRC_ACCT)
        c.post("/api/portfolio/movements/batch-reassign", json={
            "source_account_id": SRC_ACCT,
            "dest_account_id": DST_ACCT,
        })
        new_doc = _find_new_doc(fake, DST_ACCT, "mvt_noreason_02")
        assert new_doc is not None, "Reassigned doc must exist in dest account"
        assert new_doc["reassignment_reason"] == _BATCH_DEFAULT_REASON, (
            f"Expected '{_BATCH_DEFAULT_REASON}', got {new_doc['reassignment_reason']!r}"
        )

    def test_batch_empty_string_reason_returns_200(self, client):
        """reason='' (empty string) must not raise a 400."""
        c, fake = client
        _seed(fake, "mvt_empty_01", account_id=SRC_ACCT)
        resp = c.post("/api/portfolio/movements/batch-reassign", json={
            "source_account_id": SRC_ACCT,
            "dest_account_id": DST_ACCT,
            "reason": "",
        })
        assert resp.status_code == 200

    def test_batch_empty_string_reason_stores_default(self, client):
        """reason='' stored as the standard audit reason, not blank."""
        c, fake = client
        _seed(fake, "mvt_empty_02", account_id=SRC_ACCT)
        c.post("/api/portfolio/movements/batch-reassign", json={
            "source_account_id": SRC_ACCT,
            "dest_account_id": DST_ACCT,
            "reason": "",
        })
        new_doc = _find_new_doc(fake, DST_ACCT, "mvt_empty_02")
        assert new_doc is not None
        assert new_doc["reassignment_reason"] == _BATCH_DEFAULT_REASON

    def test_batch_whitespace_reason_returns_200(self, client):
        """reason='   ' (whitespace-only) must not raise a 400."""
        c, fake = client
        _seed(fake, "mvt_ws_01", account_id=SRC_ACCT)
        resp = c.post("/api/portfolio/movements/batch-reassign", json={
            "source_account_id": SRC_ACCT,
            "dest_account_id": DST_ACCT,
            "reason": "   ",
        })
        assert resp.status_code == 200

    def test_batch_whitespace_reason_stores_default(self, client):
        """Whitespace-only reason stripped to empty → stored as default audit reason."""
        c, fake = client
        _seed(fake, "mvt_ws_02", account_id=SRC_ACCT)
        c.post("/api/portfolio/movements/batch-reassign", json={
            "source_account_id": SRC_ACCT,
            "dest_account_id": DST_ACCT,
            "reason": "   ",
        })
        new_doc = _find_new_doc(fake, DST_ACCT, "mvt_ws_02")
        assert new_doc is not None
        assert new_doc["reassignment_reason"] == _BATCH_DEFAULT_REASON

    def test_batch_null_reason_returns_200(self, client):
        """reason=null treated as omitted — must not raise a 400."""
        c, fake = client
        _seed(fake, "mvt_null_01", account_id=SRC_ACCT)
        resp = c.post("/api/portfolio/movements/batch-reassign", json={
            "source_account_id": SRC_ACCT,
            "dest_account_id": DST_ACCT,
            "reason": None,
        })
        assert resp.status_code == 200

    def test_batch_null_reason_stores_default(self, client):
        """reason=null is treated as absent — stored as the standard audit reason."""
        c, fake = client
        _seed(fake, "mvt_null_02", account_id=SRC_ACCT)
        c.post("/api/portfolio/movements/batch-reassign", json={
            "source_account_id": SRC_ACCT,
            "dest_account_id": DST_ACCT,
            "reason": None,
        })
        new_doc = _find_new_doc(fake, DST_ACCT, "mvt_null_02")
        assert new_doc is not None
        assert new_doc["reassignment_reason"] == _BATCH_DEFAULT_REASON

    def test_explicit_reason_stored_verbatim(self, client):
        """An explicitly supplied reason must be stored unchanged, not overwritten."""
        c, fake = client
        _seed(fake, "mvt_expl_01", account_id=SRC_ACCT)
        c.post("/api/portfolio/movements/batch-reassign", json={
            "source_account_id": SRC_ACCT,
            "dest_account_id": DST_ACCT,
            "reason": "Correcting broker import error",
        })
        new_doc = _find_new_doc(fake, DST_ACCT, "mvt_expl_01")
        assert new_doc is not None
        assert new_doc["reassignment_reason"] == "Correcting broker import error"

    def test_individual_reassign_blank_reason_still_400(self, client):
        """Individual reassignment must still reject a blank/missing reason."""
        c, fake = client
        _seed(fake, "mvt_ind_blank", account_id=SRC_ACCT)
        resp = c.post("/api/portfolio/movements/mvt_ind_blank/reassign", json={
            "source_account_id": SRC_ACCT,
            "dest_account_id": DST_ACCT,
        })
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

    def test_individual_reassign_empty_string_reason_still_400(self, client):
        """Individual reassignment with reason='' must also reject."""
        c, fake = client
        _seed(fake, "mvt_ind_empty", account_id=SRC_ACCT)
        resp = c.post("/api/portfolio/movements/mvt_ind_empty/reassign", json={
            "source_account_id": SRC_ACCT,
            "dest_account_id": DST_ACCT,
            "reason": "",
        })
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

    def test_individual_reassign_whitespace_reason_still_400(self, client):
        """Individual reassignment with reason='  ' must also reject."""
        c, fake = client
        _seed(fake, "mvt_ind_ws", account_id=SRC_ACCT)
        resp = c.post("/api/portfolio/movements/mvt_ind_ws/reassign", json={
            "source_account_id": SRC_ACCT,
            "dest_account_id": DST_ACCT,
            "reason": "  ",
        })
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"




class TestBatchAtomicityDefect:
    def test_batch_is_atomic_on_failure(self, client):
        """
        Seed 2 valid movements + 1 that will fail mid-way.
        After the batch call, verify EITHER all 3 moved OR none moved.
        The current implementation will move the first 2 and skip the third,
        leaving a partial result — which fails this assertion.

        Strategy: monkeypatch reassign_movement to raise on the 3rd call.
        """
        c, fake = client

        _seed(fake, "mvt_atom_001", account_id=SRC_ACCT)
        _seed(fake, "mvt_atom_002", account_id=SRC_ACCT)
        _seed(fake, "mvt_atom_003", account_id=SRC_ACCT)

        from src.portfolio import cosmos_portfolio as cp
        original_reassign = cp.CosmosPortfolioService.reassign_movement
        call_count = {"n": 0}

        def failing_reassign(self, movement_id, source_account_id, dest_account_id, reason):
            call_count["n"] += 1
            if call_count["n"] == 3:
                raise RuntimeError("Simulated failure on 3rd item")
            return original_reassign(
                self, movement_id, source_account_id, dest_account_id, reason
            )

        cp.CosmosPortfolioService.reassign_movement = failing_reassign
        try:
            resp = c.post("/api/portfolio/movements/batch-reassign", json={
                "source_account_id": SRC_ACCT,
                "dest_account_id": DST_ACCT,
                "reason": "atomicity test",
            })
        finally:
            cp.CosmosPortfolioService.reassign_movement = original_reassign

        # After the call, count how many are now in DST_ACCT
        moved = sum(
            1 for v in fake.portfolio_container._store.values()
            if v.get("account_id") == DST_ACCT
        )
        # Atomic: either 0 or 3 — never 2
        assert moved in (0, 3), (
            f"Batch must be atomic: expected 0 or 3 moved, got {moved}. "
            "Partial result is a defect."
        )


# ===========================================================================
# No preview endpoint exists
# ===========================================================================

class TestNoPreviewEndpoint:
    def test_preview_endpoint_does_not_exist(self, client):
        """Confirm there is no reassign preview endpoint."""
        c, _ = client
        resp = c.get("/api/portfolio/movements/reassign/preview?source_account_id=_unassigned")
        assert resp.status_code == 404, (
            "No preview endpoint exists; callers must use batch-reassign directly. "
            "If this test starts failing, it means a preview endpoint was added — "
            "update the contract tests accordingly."
        )
