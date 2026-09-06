"""Phase 2 regression tests — FX rates endpoint.

Covers:
- GET /api/fx/rates?from_currency=USD&to_currency=EUR&date=2024-06-15
- EUR→EUR: returns rate "1.000000000" without network call
- Successful rate lookup (mocked ECB response)
- to_currency != EUR → 400
- from_currency missing → 400
- Invalid date format → 400
- Currency not found in ECB data → 404 (rate_not_found)
- ECB service unavailable → 503 (fx_unavailable)
- date param optional (defaults to today — uses cache/ECB)

The service makes real HTTP calls to ECB. All tests (except EUR→EUR)
mock `src.portfolio.fx_service.get_fx_rate` to avoid network dependency.
"""

from __future__ import annotations

import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

from tests.conftest_portfolio_p2 import FakeCosmos

# Exception classes from fx_service
from src.portfolio.fx_service import FxRateNotFoundError, FxUnavailableError


@pytest.fixture
def client():
    from web.app import app
    fake = FakeCosmos()
    with TestClient(app) as c:
        app.state.cosmos = fake
        app.state.cosmos_error = None
        yield c, fake


def _mock_rate(c, from_currency, rate, date="2024-06-15"):
    """Patch get_fx_rate where the route uses it and call the FX endpoint."""
    rate_str = str(Decimal(str(rate)).quantize(Decimal("0.000000001")))
    with patch("web.portfolio_routes.get_fx_rate", return_value=rate_str):
        return c.get(
            f"/api/fx/rates?from_currency={from_currency}&to_currency=EUR&date={date}"
        )


def _mock_rate_error(c, from_currency, exc, date="2024-06-15"):
    """Patch get_fx_rate to raise an exception and call the FX endpoint."""
    with patch("web.portfolio_routes.get_fx_rate", side_effect=exc):
        return c.get(
            f"/api/fx/rates?from_currency={from_currency}&to_currency=EUR&date={date}"
        )


# ===========================================================================
# EUR → EUR: no network call needed
# ===========================================================================

class TestEurToEur:
    def test_eur_eur_returns_one(self, client):
        c, _ = client
        resp = c.get("/api/fx/rates?from_currency=EUR&to_currency=EUR&date=2024-06-15")
        assert resp.status_code == 200
        data = resp.json()
        assert Decimal(data["rate"]) == Decimal("1")

    def test_eur_eur_from_currency_preserved(self, client):
        c, _ = client
        resp = c.get("/api/fx/rates?from_currency=EUR&to_currency=EUR&date=2024-06-15")
        assert resp.status_code == 200
        assert resp.json()["from_currency"] == "EUR"

    def test_eur_eur_to_currency_preserved(self, client):
        c, _ = client
        resp = c.get("/api/fx/rates?from_currency=EUR&to_currency=EUR&date=2024-06-15")
        assert resp.status_code == 200
        assert resp.json()["to_currency"] == "EUR"


# ===========================================================================
# Successful rate lookup (non-EUR source)
# ===========================================================================

class TestSuccessfulRateLookup:
    def test_usd_rate_200(self, client):
        c, _ = client
        resp = _mock_rate(c, "USD", "0.921500000")
        assert resp.status_code == 200

    def test_response_has_rate_field(self, client):
        c, _ = client
        resp = _mock_rate(c, "USD", "0.921500000")
        assert resp.status_code == 200
        assert "rate" in resp.json()

    def test_response_rate_value(self, client):
        c, _ = client
        resp = _mock_rate(c, "USD", "0.921500000")
        assert resp.status_code == 200
        assert Decimal(resp.json()["rate"]) == Decimal("0.921500000")

    def test_response_has_from_currency(self, client):
        c, _ = client
        resp = _mock_rate(c, "USD", "0.921500000")
        assert resp.status_code == 200
        assert resp.json()["from_currency"] == "USD"

    def test_response_has_to_currency(self, client):
        c, _ = client
        resp = _mock_rate(c, "USD", "0.921500000")
        assert resp.status_code == 200
        assert resp.json()["to_currency"] == "EUR"

    def test_response_has_date(self, client):
        c, _ = client
        resp = _mock_rate(c, "USD", "0.921500000", date="2024-06-15")
        assert resp.status_code == 200
        assert "date" in resp.json()

    def test_response_has_rate_source_ecb(self, client):
        c, _ = client
        resp = _mock_rate(c, "USD", "0.921500000")
        assert resp.status_code == 200
        assert resp.json().get("rate_source") == "ECB"

    def test_response_note_is_null_or_string(self, client):
        c, _ = client
        resp = _mock_rate(c, "USD", "0.921500000")
        assert resp.status_code == 200
        note = resp.json().get("note")
        assert note is None or isinstance(note, str)

    def test_gbp_rate(self, client):
        c, _ = client
        resp = _mock_rate(c, "GBP", "1.16430000")
        assert resp.status_code == 200
        assert Decimal(resp.json()["rate"]) == Decimal("1.16430000")

    def test_jpy_rate(self, client):
        c, _ = client
        resp = _mock_rate(c, "JPY", "0.00625000")
        assert resp.status_code == 200


# ===========================================================================
# Validation errors (400)
# ===========================================================================

class TestFxValidationErrors:
    def test_to_currency_not_eur_400(self, client):
        c, _ = client
        resp = c.get("/api/fx/rates?from_currency=USD&to_currency=GBP&date=2024-06-15")
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"] in ("validation_error", "unsupported_conversion")

    def test_from_currency_missing_422(self, client):
        """FastAPI returns 422 (not 400) when a required Query param is absent."""
        c, _ = client
        resp = c.get("/api/fx/rates?to_currency=EUR&date=2024-06-15")
        assert resp.status_code == 422

    def test_invalid_date_format_400(self, client):
        c, _ = client
        resp = c.get("/api/fx/rates?from_currency=USD&to_currency=EUR&date=15-06-2024")
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"

    def test_invalid_date_format_words_400(self, client):
        c, _ = client
        resp = c.get("/api/fx/rates?from_currency=USD&to_currency=EUR&date=yesterday")
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"


# ===========================================================================
# Rate not found (404)
# ===========================================================================

class TestFxRateNotFound:
    def test_unsupported_currency_404(self, client):
        c, _ = client
        resp = _mock_rate_error(
            c, "XYZ",
            FxRateNotFoundError("XYZ", "2024-06-15"),
            date="2024-06-15",
        )
        assert resp.status_code == 404
        assert resp.json()["error"] in ("rate_not_found", "not_found")

    def test_currency_with_no_ecb_data_404(self, client):
        """VES (Venezuelan bolívar) is not published by ECB."""
        c, _ = client
        resp = _mock_rate_error(
            c, "VES",
            FxRateNotFoundError("VES", "2024-06-15"),
        )
        assert resp.status_code == 404

    def test_future_date_rate_not_found_404(self, client):
        """ECB has no data for future dates; this resolves to 404."""
        c, _ = client
        resp = _mock_rate_error(
            c, "USD",
            FxRateNotFoundError("USD", "2099-01-01"),
            date="2099-01-01",
        )
        assert resp.status_code == 404


# ===========================================================================
# ECB unavailable (503)
# ===========================================================================

class TestFxServiceUnavailable:
    def test_ecb_unavailable_503(self, client):
        c, _ = client
        resp = _mock_rate_error(
            c, "USD",
            FxUnavailableError("ECB endpoint unreachable"),
        )
        assert resp.status_code == 503

    def test_ecb_unavailable_error_code(self, client):
        c, _ = client
        resp = _mock_rate_error(
            c, "USD",
            FxUnavailableError("ECB endpoint unreachable"),
        )
        assert resp.status_code == 503
        assert resp.json()["error"] == "fx_unavailable"

    def test_ecb_unavailable_has_retry_after_or_message(self, client):
        c, _ = client
        resp = _mock_rate_error(
            c, "USD",
            FxUnavailableError("ECB endpoint unreachable"),
        )
        assert resp.status_code == 503
        data = resp.json()
        assert "message" in data or "retry_after" in data or "details" in data or "detail" in data


# ===========================================================================
# Optional date (defaults to today)
# ===========================================================================

class TestFxOptionalDate:
    def test_no_date_param_200(self, client):
        c, _ = client
        rate_str = str(Decimal("0.9215").quantize(Decimal("0.000000001")))
        with patch("web.portfolio_routes.get_fx_rate", return_value=rate_str):
            resp = c.get("/api/fx/rates?from_currency=USD&to_currency=EUR")
        assert resp.status_code == 200

    def test_eur_eur_no_date_200(self, client):
        c, _ = client
        resp = c.get("/api/fx/rates?from_currency=EUR&to_currency=EUR")
        assert resp.status_code == 200
        assert Decimal(resp.json()["rate"]) == Decimal("1")
