"""Regression tests — Account assignment in Symbol Details (directive 2026-09-06).

Contract:
  SD-1  POST /api/portfolio/movements/batch-reassign/preview endpoint exists.
  SD-2  Preview response contains required fields: affected_count, sample,
        movement_ids, source_account_id, dest_account_id.
  SD-3  Preview with security_id filter counts only that security's movements.
  SD-4  Preview with security_id filter excludes other securities from count.
  SD-5  Preview missing source_account_id → 400 validation_error.
  SD-6  Preview missing dest_account_id → 400 validation_error.
  SD-7  Preview same source == dest → 400 validation_error.
  SD-8  Preview source_account_id reflected in response.
  SD-9  Preview dest_account_id reflected in response.
  SD-10 Preview empty source returns affected_count == 0.
  SD-11 Execution with security_id scope moves only that security's movements.
  SD-12 Execution with security_id scope leaves other securities untouched.
  SD-13 Execution scoped: affected_count from preview matches reassigned_count in exec.
  SD-14 Individual reassignment endpoint remains available after directive.
  SD-15 Batch reassignment endpoint still reachable (not removed by directive change).
  SD-16 Preview sample entries include security_id field for UI display.
  SD-17 Preview sample entries include trade_date and txn_type for UI display.
  SD-18 Preview is read-only: no movements are mutated by preview call.
  SD-19 Preview security_id filter with date range — joint scoping works.
  SD-20 Execution with only security_id (no dates) — all dates in scope.
"""

from __future__ import annotations

import pytest
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


SRC = "_unassigned"
DST = "acct_heytrade_main"
AAPL = "XNYS:AAPL"
SAN = "XMAD:SAN"


def _seed(fake, mid, account_id=SRC, security_id=AAPL,
          txn_type="BUY", trade_date="2024-06-01"):
    doc = {
        "id": mid,
        "doc_type": "ledger_txn",
        "txn_type": txn_type,
        "security_id": security_id,
        "ticker": security_id.split(":")[-1],
        "trade_date": trade_date,
        "quantity": "100",
        "gross": {"amount": "5000", "currency": "EUR", "eur_amount": "5000"},
        "fees": {"total": "0", "currency": "EUR", "total_eur": "0"},
        "net": {"amount": "5000", "currency": "EUR", "eur_amount": "5000"},
        "account_id": account_id,
        "correction_status": "ACTIVE",
        "import_source": "manual",
        "created_at": "2026-09-06T10:00:00Z",
        "warnings": [],
    }
    fake.portfolio_container._store[mid] = doc
    return doc


# ===========================================================================
# SD-1 / SD-2: Preview endpoint exists and returns required fields
# ===========================================================================

class TestPreviewEndpointContract:
    def test_sd1_preview_endpoint_exists(self, client):
        """SD-1: POST batch-reassign/preview returns 200 (not 404)."""
        c, fake = client
        _seed(fake, "sd1_mvt")
        resp = c.post("/api/portfolio/movements/batch-reassign/preview", json={
            "source_account_id": SRC,
            "dest_account_id": DST,
        })
        assert resp.status_code == 200, (
            "SD-1: Preview endpoint must exist. Got 404 — endpoint not registered."
        )

    def test_sd2_preview_has_affected_count(self, client):
        """SD-2: Response must contain affected_count."""
        c, fake = client
        _seed(fake, "sd2_mvt_a")
        resp = c.post("/api/portfolio/movements/batch-reassign/preview", json={
            "source_account_id": SRC,
            "dest_account_id": DST,
        })
        assert resp.status_code == 200
        assert "affected_count" in resp.json(), "SD-2: 'affected_count' missing from preview response"

    def test_sd2_preview_has_sample(self, client):
        """SD-2: Response must contain sample list."""
        c, fake = client
        _seed(fake, "sd2_mvt_b")
        resp = c.post("/api/portfolio/movements/batch-reassign/preview", json={
            "source_account_id": SRC,
            "dest_account_id": DST,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "sample" in data, "SD-2: 'sample' missing from preview response"
        assert isinstance(data["sample"], list), "SD-2: 'sample' must be a list"

    def test_sd2_preview_has_movement_ids(self, client):
        """SD-2: Response must contain movement_ids."""
        c, fake = client
        _seed(fake, "sd2_mvt_c")
        resp = c.post("/api/portfolio/movements/batch-reassign/preview", json={
            "source_account_id": SRC,
            "dest_account_id": DST,
        })
        assert resp.status_code == 200
        assert "movement_ids" in resp.json(), "SD-2: 'movement_ids' missing from preview response"

    def test_sd8_preview_reflects_source_account(self, client):
        """SD-8: source_account_id echoed in response."""
        c, fake = client
        _seed(fake, "sd8_mvt")
        resp = c.post("/api/portfolio/movements/batch-reassign/preview", json={
            "source_account_id": SRC,
            "dest_account_id": DST,
        })
        assert resp.status_code == 200
        assert resp.json().get("source_account_id") == SRC, "SD-8: source_account_id not reflected in response"

    def test_sd9_preview_reflects_dest_account(self, client):
        """SD-9: dest_account_id echoed in response."""
        c, fake = client
        _seed(fake, "sd9_mvt")
        resp = c.post("/api/portfolio/movements/batch-reassign/preview", json={
            "source_account_id": SRC,
            "dest_account_id": DST,
        })
        assert resp.status_code == 200
        assert resp.json().get("dest_account_id") == DST, "SD-9: dest_account_id not reflected in response"


# ===========================================================================
# SD-3 / SD-4: security_id scoping in preview
# ===========================================================================

class TestPreviewSecurityIdScoping:
    def test_sd3_preview_scoped_counts_only_target_security(self, client):
        """SD-3: Preview with security_id only counts movements for that security."""
        c, fake = client
        _seed(fake, "sd3_aapl_1", security_id=AAPL)
        _seed(fake, "sd3_aapl_2", security_id=AAPL)
        _seed(fake, "sd3_san_1", security_id=SAN)
        resp = c.post("/api/portfolio/movements/batch-reassign/preview", json={
            "source_account_id": SRC,
            "dest_account_id": DST,
            "security_id": AAPL,
        })
        assert resp.status_code == 200
        assert resp.json()["affected_count"] == 2, (
            "SD-3: Preview scoped to AAPL should count 2, not 3"
        )

    def test_sd4_preview_excludes_other_security(self, client):
        """SD-4: Preview scoped to AAPL should not include SAN movements."""
        c, fake = client
        _seed(fake, "sd4_aapl_1", security_id=AAPL)
        _seed(fake, "sd4_san_1", security_id=SAN)
        _seed(fake, "sd4_san_2", security_id=SAN)
        resp = c.post("/api/portfolio/movements/batch-reassign/preview", json={
            "source_account_id": SRC,
            "dest_account_id": DST,
            "security_id": AAPL,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["affected_count"] == 1, (
            "SD-4: Only AAPL counted; SAN movements must not be included"
        )
        # Sample entries must all be for AAPL
        for item in data["sample"]:
            assert item["security_id"] == AAPL, (
                f"SD-4: Sample entry has security_id={item['security_id']}, expected {AAPL}"
            )

    def test_sd10_preview_empty_source_returns_zero(self, client):
        """SD-10: No movements in source account → affected_count == 0."""
        c, _ = client
        resp = c.post("/api/portfolio/movements/batch-reassign/preview", json={
            "source_account_id": "acct_empty_src",
            "dest_account_id": DST,
        })
        assert resp.status_code == 200
        assert resp.json()["affected_count"] == 0


# ===========================================================================
# SD-5 / SD-6 / SD-7: Preview validation errors
# ===========================================================================

class TestPreviewValidation:
    def test_sd5_preview_missing_source_400(self, client):
        """SD-5: Missing source_account_id → 400 validation_error."""
        c, _ = client
        resp = c.post("/api/portfolio/movements/batch-reassign/preview", json={
            "dest_account_id": DST,
        })
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error", (
            "SD-5: missing source_account_id must be validation_error"
        )

    def test_sd6_preview_missing_dest_400(self, client):
        """SD-6: Missing dest_account_id → 400 validation_error."""
        c, _ = client
        resp = c.post("/api/portfolio/movements/batch-reassign/preview", json={
            "source_account_id": SRC,
        })
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error", (
            "SD-6: missing dest_account_id must be validation_error"
        )

    def test_sd7_preview_same_src_dest_400(self, client):
        """SD-7: source == dest → 400 validation_error."""
        c, _ = client
        resp = c.post("/api/portfolio/movements/batch-reassign/preview", json={
            "source_account_id": SRC,
            "dest_account_id": SRC,
        })
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error", (
            "SD-7: same source/dest must be validation_error"
        )


# ===========================================================================
# SD-11 / SD-12: Execution with security_id scope
# ===========================================================================

class TestExecutionSecurityIdScoping:
    def test_sd11_execution_moves_only_scoped_security(self, client):
        """SD-11: Batch exec with security_id=AAPL moves only AAPL movements."""
        c, fake = client
        _seed(fake, "sd11_aapl_1", security_id=AAPL)
        _seed(fake, "sd11_aapl_2", security_id=AAPL)
        _seed(fake, "sd11_san_1", security_id=SAN)

        resp = c.post("/api/portfolio/movements/batch-reassign", json={
            "source_account_id": SRC,
            "dest_account_id": DST,
            "security_id": AAPL,
        })
        assert resp.status_code == 200
        assert resp.json()["reassigned_count"] == 2, (
            "SD-11: Only 2 AAPL movements should be reassigned"
        )

    def test_sd12_execution_does_not_touch_other_security(self, client):
        """SD-12: Batch exec scoped to AAPL leaves SAN movements in original account."""
        c, fake = client
        _seed(fake, "sd12_aapl_1", security_id=AAPL)
        _seed(fake, "sd12_san_1", security_id=SAN)
        _seed(fake, "sd12_san_2", security_id=SAN)

        c.post("/api/portfolio/movements/batch-reassign", json={
            "source_account_id": SRC,
            "dest_account_id": DST,
            "security_id": AAPL,
        })

        # SAN movements must still be in original source account
        san_still_in_src = [
            v for v in fake.portfolio_container._store.values()
            if v.get("security_id") == SAN
            and v.get("account_id") == SRC
            and v.get("correction_status", "ACTIVE") == "ACTIVE"
        ]
        assert len(san_still_in_src) == 2, (
            f"SD-12: Both SAN movements must remain in {SRC!r}. "
            f"Found {len(san_still_in_src)}"
        )

    def test_sd13_preview_count_matches_execution_count(self, client):
        """SD-13: affected_count from preview should equal reassigned_count in execution."""
        c, fake = client
        for i in range(4):
            _seed(fake, f"sd13_aapl_{i}", security_id=AAPL, trade_date=f"2024-0{i+1}-01")
        _seed(fake, "sd13_san_1", security_id=SAN)

        preview_resp = c.post("/api/portfolio/movements/batch-reassign/preview", json={
            "source_account_id": SRC,
            "dest_account_id": DST,
            "security_id": AAPL,
        })
        assert preview_resp.status_code == 200
        preview_count = preview_resp.json()["affected_count"]

        exec_resp = c.post("/api/portfolio/movements/batch-reassign", json={
            "source_account_id": SRC,
            "dest_account_id": DST,
            "security_id": AAPL,
        })
        assert exec_resp.status_code == 200
        assert exec_resp.json()["reassigned_count"] == preview_count, (
            "SD-13: Execution count must equal preview count (same predicate, same data)"
        )


# ===========================================================================
# SD-16 / SD-17 / SD-18: Sample entry fields and read-only guarantee
# ===========================================================================

class TestPreviewSampleFields:
    def test_sd16_sample_entries_include_security_id(self, client):
        """SD-16: Each sample entry must include security_id for UI display."""
        c, fake = client
        _seed(fake, "sd16_mvt_1", security_id=AAPL)
        _seed(fake, "sd16_mvt_2", security_id=AAPL)

        resp = c.post("/api/portfolio/movements/batch-reassign/preview", json={
            "source_account_id": SRC,
            "dest_account_id": DST,
        })
        assert resp.status_code == 200
        for item in resp.json()["sample"]:
            assert "security_id" in item, (
                f"SD-16: Sample entry missing security_id: {item}"
            )

    def test_sd17_sample_entries_include_trade_date_and_txn_type(self, client):
        """SD-17: Each sample entry must include trade_date and txn_type."""
        c, fake = client
        _seed(fake, "sd17_mvt", security_id=AAPL, txn_type="SELL", trade_date="2024-09-01")

        resp = c.post("/api/portfolio/movements/batch-reassign/preview", json={
            "source_account_id": SRC,
            "dest_account_id": DST,
        })
        assert resp.status_code == 200
        sample = resp.json()["sample"]
        assert len(sample) >= 1
        entry = sample[0]
        assert "trade_date" in entry, "SD-17: Sample entry missing trade_date"
        assert "txn_type" in entry, "SD-17: Sample entry missing txn_type"

    def test_sd18_preview_is_readonly_no_mutations(self, client):
        """SD-18: Preview call must not move or alter any movements."""
        c, fake = client
        _seed(fake, "sd18_mvt_1", security_id=AAPL)
        _seed(fake, "sd18_mvt_2", security_id=SAN)

        store_before = dict(fake.portfolio_container._store)

        resp = c.post("/api/portfolio/movements/batch-reassign/preview", json={
            "source_account_id": SRC,
            "dest_account_id": DST,
        })
        assert resp.status_code == 200

        store_after = fake.portfolio_container._store
        assert set(store_after.keys()) == set(store_before.keys()), (
            "SD-18: Preview must not create or delete any movements"
        )
        for key in store_before:
            assert store_after[key]["account_id"] == store_before[key]["account_id"], (
                f"SD-18: Preview mutated account_id of movement {key!r}"
            )


# ===========================================================================
# SD-19: Joint security_id + date_range scoping
# ===========================================================================

class TestPreviewJointScoping:
    def test_sd19_preview_security_and_date_range(self, client):
        """SD-19: security_id + date_from/date_to — only matching subset counted."""
        c, fake = client
        _seed(fake, "sd19_aapl_jan", security_id=AAPL, trade_date="2024-01-15")
        _seed(fake, "sd19_aapl_sep", security_id=AAPL, trade_date="2024-09-01")
        _seed(fake, "sd19_san_sep", security_id=SAN, trade_date="2024-09-01")

        resp = c.post("/api/portfolio/movements/batch-reassign/preview", json={
            "source_account_id": SRC,
            "dest_account_id": DST,
            "security_id": AAPL,
            "date_from": "2024-06-01",
            "date_to": "2024-12-31",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["affected_count"] == 1, (
            "SD-19: Only AAPL September should be in scope (not Jan, not SAN)"
        )
        assert data["sample"][0]["trade_date"] == "2024-09-01"

    def test_sd20_execution_no_dates_all_dates_in_scope(self, client):
        """SD-20: security_id without dates — all dates for that security reassigned."""
        c, fake = client
        _seed(fake, "sd20_aapl_2022", security_id=AAPL, trade_date="2022-03-10")
        _seed(fake, "sd20_aapl_2024", security_id=AAPL, trade_date="2024-11-05")
        _seed(fake, "sd20_san_2024", security_id=SAN, trade_date="2024-11-05")

        resp = c.post("/api/portfolio/movements/batch-reassign", json={
            "source_account_id": SRC,
            "dest_account_id": DST,
            "security_id": AAPL,
        })
        assert resp.status_code == 200
        assert resp.json()["reassigned_count"] == 2, (
            "SD-20: Both AAPL movements (regardless of date) must be reassigned"
        )


# ===========================================================================
# SD-14 / SD-15: Individual reassignment and batch endpoint still available
# ===========================================================================

class TestEndpointsStillAvailable:
    def test_sd14_individual_reassign_endpoint_available(self, client):
        """SD-14: Individual reassignment endpoint remains reachable after directive."""
        c, fake = client
        _seed(fake, "sd14_mvt", account_id=SRC, security_id=AAPL)
        resp = c.post("/api/portfolio/movements/sd14_mvt/reassign", json={
            "source_account_id": SRC,
            "dest_account_id": DST,
            "reason": "moved to correct account",
        })
        assert resp.status_code == 200, (
            "SD-14: Individual reassignment endpoint must remain available"
        )

    def test_sd15_batch_reassign_endpoint_available(self, client):
        """SD-15: Generic batch-reassign endpoint is not removed by directive."""
        c, fake = client
        _seed(fake, "sd15_mvt", account_id=SRC)
        resp = c.post("/api/portfolio/movements/batch-reassign", json={
            "source_account_id": SRC,
            "dest_account_id": DST,
        })
        assert resp.status_code == 200, (
            "SD-15: Batch reassign endpoint must remain available"
        )
