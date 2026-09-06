"""Tests for ensure_symbol_config — Symbol Unification rev 3.

Contract: backend/src/portfolio/symbol_config_sync.py (to be implemented by Livingston).

Coverage:
- Idempotent create / already-existed / race-condition (409→re-read)
- Every new config flag is explicitly false/off
- Existing configs are never modified (byte-for-byte preserved)
- security_id field set on new configs only
- Collision warning emitted when existing config security_id differs
- ValueError on missing security_master
- All invariants from Danny's contract §1

All tests are hermetic — FakeContainer, no network.
"""

from __future__ import annotations

import logging
import pytest
from unittest.mock import patch
from azure.cosmos.exceptions import CosmosResourceNotFoundError, CosmosHttpResponseError

# ---------------------------------------------------------------------------
# ImportError surfaces immediately if Livingston hasn't implemented yet
# ---------------------------------------------------------------------------
from src.portfolio.symbol_config_sync import ensure_symbol_config  # noqa: E402
from src.portfolio.cosmos_securities import CosmosSecuritiesService


# ---------------------------------------------------------------------------
# Fake Cosmos containers
# ---------------------------------------------------------------------------

class FakeSymbolsContainer:
    """In-memory symbols container that stores both symbol_config and security_master docs."""

    def __init__(self):
        self._store: dict = {}  # key: (partition_key, id) → doc

    def read_item(self, item: str, partition_key: str):
        key = (partition_key, item)
        if key not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        return dict(self._store[key])

    def create_item(self, body: dict):
        ticker = body["symbol"]
        key = (ticker, body["id"])
        if key in self._store:
            raise CosmosHttpResponseError(status_code=409, message="Conflict")
        self._store[key] = dict(body)
        return dict(body)

    def query_items(self, query: str = "", parameters=None,
                    enable_cross_partition_query=False, partition_key=None):
        param_map = {p["name"]: p["value"] for p in (parameters or [])}
        results = []
        for (pk, did), doc in self._store.items():
            if partition_key and pk != partition_key:
                continue
            if doc.get("doc_type") != "security_master":
                continue
            if "@isin" in param_map and doc.get("isin") != param_map["@isin"]:
                continue
            results.append(dict(doc))
        return iter(results)

    def replace_item(self, item: str, body: dict):
        for key, stored in self._store.items():
            if stored.get("id") == item:
                self._store[key] = dict(body)
                return dict(body)
        raise CosmosResourceNotFoundError(message="not found", response=None)

    def upsert_item(self, body: dict):
        ticker = body.get("symbol", "")
        key = (ticker, body["id"])
        self._store[key] = dict(body)
        return dict(body)

    # Helper: pre-seed a security_master so ensure_symbol_config can resolve it
    def seed_security(self, security_id: str, company_name: str = "Test Co."):
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

    # Helper: pre-seed a symbol_config
    def seed_config(self, ticker: str, extra: dict | None = None):
        doc = {
            "id": f"config_{ticker}",
            "symbol": ticker,
            "doc_type": "symbol_config",
        }
        if extra:
            doc.update(extra)
        self._store[(ticker, doc["id"])] = doc
        return doc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_container() -> FakeSymbolsContainer:
    return FakeSymbolsContainer()


# ---------------------------------------------------------------------------
# §1 — create semantics
# ---------------------------------------------------------------------------

class TestEnsureSymbolConfigCreate:
    def test_creates_config_when_absent(self):
        ctr = _make_container()
        ctr.seed_security("XNYS:AAPL", "Apple Inc.")
        result = ensure_symbol_config(ctr, "XNYS:AAPL", source="add_symbol")
        assert result["id"] == "config_AAPL"
        assert result["symbol"] == "AAPL"
        assert result["doc_type"] == "symbol_config"

    def test_new_config_has_correct_security_id(self):
        ctr = _make_container()
        ctr.seed_security("XNYS:AAPL", "Apple Inc.")
        result = ensure_symbol_config(ctr, "XNYS:AAPL", source="add_symbol")
        assert result["security_id"] == "XNYS:AAPL"

    def test_new_config_has_correct_exchange(self):
        ctr = _make_container()
        ctr.seed_security("XMAD:TEF", "Telefónica")
        result = ensure_symbol_config(ctr, "XMAD:TEF", source="import_commit")
        assert result["exchange"] == "XMAD"

    def test_new_config_display_name_from_security_master(self):
        ctr = _make_container()
        ctr.seed_security("XNYS:AAPL", "Apple Inc.")
        result = ensure_symbol_config(ctr, "XNYS:AAPL", source="add_symbol")
        assert result["display_name"] == "Apple Inc."

    def test_new_config_auto_enrolled_fields(self):
        ctr = _make_container()
        ctr.seed_security("XNYS:AAPL", "Apple Inc.")
        result = ensure_symbol_config(ctr, "XNYS:AAPL", source="manual_movement")
        assert result["_auto_enrolled"] is True
        assert result["_auto_enrolled_source"] == "manual_movement"
        assert "_auto_enrolled_at" in result

    def test_new_config_total_shares_zero(self):
        ctr = _make_container()
        ctr.seed_security("XNYS:AAPL", "Apple Inc.")
        result = ensure_symbol_config(ctr, "XNYS:AAPL", source="add_symbol")
        assert result["total_shares"] == 0

    def test_new_config_positions_empty_list(self):
        ctr = _make_container()
        ctr.seed_security("XNYS:AAPL", "Apple Inc.")
        result = ensure_symbol_config(ctr, "XNYS:AAPL", source="add_symbol")
        assert result["positions"] == []


# ---------------------------------------------------------------------------
# §1 — disabled-defaults invariant (AC-2, AC-3)
# ---------------------------------------------------------------------------

class TestEnsureSymbolConfigDisabledDefaults:
    """Every agent, alert, notification flag must be explicitly false on creation."""

    def _get_new_config(self, security_id="XNYS:AAPL"):
        ctr = _make_container()
        ctr.seed_security(security_id, "Test Co")
        return ensure_symbol_config(ctr, security_id, source="add_symbol")

    def test_telegram_notifications_disabled(self):
        cfg = self._get_new_config()
        assert cfg["telegram_notifications_enabled"] is False

    def test_watchlist_covered_call_disabled(self):
        cfg = self._get_new_config()
        assert cfg["watchlist"]["covered_call"] is False

    def test_watchlist_cash_secured_put_disabled(self):
        cfg = self._get_new_config()
        assert cfg["watchlist"]["cash_secured_put"] is False

    def test_watchlist_buy_tracker_disabled(self):
        cfg = self._get_new_config()
        assert cfg["watchlist"]["buy_tracker"] is False

    def test_no_extra_agent_flags_enabled(self):
        """Any additional agent/schedule/automation flags introduced in the future
        must also default to False. Verify watchlist is an exhaustive mapping of
        falsy values."""
        cfg = self._get_new_config()
        wl = cfg.get("watchlist", {})
        for flag_name, flag_val in wl.items():
            assert flag_val is False or flag_val == 0, (
                f"Watchlist flag '{flag_name}' must be False on new config, got {flag_val!r}"
            )


# ---------------------------------------------------------------------------
# §1 — idempotency
# ---------------------------------------------------------------------------

class TestEnsureSymbolConfigIdempotency:
    def test_second_call_returns_same_doc(self):
        ctr = _make_container()
        ctr.seed_security("XNYS:AAPL", "Apple Inc.")
        first = ensure_symbol_config(ctr, "XNYS:AAPL", source="add_symbol")
        second = ensure_symbol_config(ctr, "XNYS:AAPL", source="add_symbol")
        assert second["id"] == first["id"]
        assert second["security_id"] == first["security_id"]

    def test_existing_config_not_modified(self):
        """Existing config preserved byte-for-byte; ensure must not overwrite."""
        ctr = _make_container()
        ctr.seed_security("XNYS:AAPL", "Apple Inc.")
        # Seed existing config with agent toggles ON and custom fields
        original = ctr.seed_config("AAPL", {
            "telegram_notifications_enabled": True,
            "watchlist": {"covered_call": True, "cash_secured_put": True, "buy_tracker": True},
            "total_shares": 500,
            "positions": [{"position_id": "pos_001", "type": "call"}],
            "custom_field": "keep_me",
        })
        result = ensure_symbol_config(ctr, "XNYS:AAPL", source="add_symbol")
        # Must return the ORIGINAL, untouched
        assert result["telegram_notifications_enabled"] is True
        assert result["watchlist"]["covered_call"] is True
        assert result["total_shares"] == 500
        assert result["custom_field"] == "keep_me"
        assert len(result["positions"]) == 1

    def test_source_field_not_updated_on_existing_config(self):
        """_auto_enrolled_source must NOT be overwritten on re-call."""
        ctr = _make_container()
        ctr.seed_security("XNYS:AAPL", "Apple Inc.")
        ctr.seed_config("AAPL", {"_auto_enrolled_source": "import_commit"})
        result = ensure_symbol_config(ctr, "XNYS:AAPL", source="manual_movement")
        # Source must still reflect original enrollment
        assert result.get("_auto_enrolled_source") != "manual_movement"


# ---------------------------------------------------------------------------
# §1 — race condition (409 → re-read)
# ---------------------------------------------------------------------------

class TestEnsureSymbolConfigRaceCondition:
    def test_409_on_create_returns_existing_doc(self):
        """When Cosmos returns 409 on create, the function re-reads and returns existing."""
        ctr = _make_container()
        ctr.seed_security("XNYS:AAPL", "Apple Inc.")
        # Pre-seed config so re-read succeeds
        existing = ctr.seed_config("AAPL", {"security_id": "XNYS:AAPL"})

        # Make create_item always raise 409
        original_create = ctr.create_item

        def _conflict_create(body):
            raise CosmosHttpResponseError(status_code=409, message="Conflict")

        ctr.create_item = _conflict_create
        result = ensure_symbol_config(ctr, "XNYS:AAPL", source="add_symbol")
        # Must return the pre-existing config, not re-raise
        assert result["id"] == existing["id"]


# ---------------------------------------------------------------------------
# §1 — error handling
# ---------------------------------------------------------------------------

class TestEnsureSymbolConfigErrors:
    def test_missing_security_master_raises_value_error(self):
        """If security_master doesn't exist, ensure must raise ValueError."""
        ctr = _make_container()  # no security seeded
        with pytest.raises(ValueError):
            ensure_symbol_config(ctr, "XNYS:AAPL", source="add_symbol")

    def test_collision_warning_logged_when_security_id_differs(self, caplog):
        """Existing config with DIFFERENT security_id: no-op + log warning."""
        ctr = _make_container()
        ctr.seed_security("XNYS:AAPL", "Apple Inc.")
        # Existing config for ticker AAPL but linked to a DIFFERENT security_id
        ctr.seed_config("AAPL", {"security_id": "XNAS:AAPL"})

        with caplog.at_level(logging.WARNING):
            result = ensure_symbol_config(ctr, "XNYS:AAPL", source="add_symbol")

        # Must return existing (no-op)
        assert result["security_id"] == "XNAS:AAPL"
        # Must have warned
        assert any("collision" in m.lower() or "ticker" in m.lower() or "security_id" in m.lower()
                   for m in caplog.messages), (
            f"Expected collision/ticker/security_id warning, got: {caplog.messages}"
        )

    def test_transient_error_propagates(self):
        """429 / 503 from Cosmos propagates; caller handles retry."""
        ctr = _make_container()
        ctr.seed_security("XNYS:AAPL", "Apple Inc.")

        def _transient_read(item, partition_key):
            raise CosmosHttpResponseError(status_code=503, message="Service Unavailable")

        ctr.read_item = _transient_read
        with pytest.raises(CosmosHttpResponseError):
            ensure_symbol_config(ctr, "XNYS:AAPL", source="add_symbol")
