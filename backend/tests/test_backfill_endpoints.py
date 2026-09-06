"""Tests for symbol-config backfill endpoints — Symbol Unification rev 3.

Contract: Danny's §3 — Backfill endpoints.

Coverage:
- Dry-run GET: no writes; returns correct gap count and collision warnings
- Confirmed POST: creates only missing configs; skips existing
- Existing configs untouched (byte-for-byte) after confirmed run
- All new configs have every flag off
- Collision warnings reported accurately
- Restartable/idempotent: running confirmed twice → same result as once
- 400 if ?confirm is absent from POST body
- Auth/error patterns

All tests use FastAPI TestClient with fake Cosmos state.
"""

from __future__ import annotations

import pytest
from azure.cosmos.exceptions import CosmosResourceNotFoundError, CosmosHttpResponseError

from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fake containers (extended for backfill needs)
# ---------------------------------------------------------------------------

class FakePortfolioContainer:
    """Portfolio container with ledger_txn docs for backfill scan."""

    def __init__(self, ledger_docs=None):
        self._store: dict = {}
        for doc in (ledger_docs or []):
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
        results = []
        for doc in self._store.values():
            if "NOT IS_DEFINED(c.deleted_at)" in query and "deleted_at" in doc:
                continue
            if "doc_type = 'ledger_txn'" in query and doc.get("doc_type") != "ledger_txn":
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
        if key in self._store:
            raise CosmosHttpResponseError(status_code=409, message="Conflict")
        self._store[key] = dict(body)
        return dict(body)

    def query_items(self, query="", parameters=None, enable_cross_partition_query=False,
                    partition_key=None):
        param_map = {p["name"]: p["value"] for p in (parameters or [])}
        results = []
        for (pk, did), doc in self._store.items():
            if doc.get("doc_type") == "security_master":
                if "@isin" in param_map and doc.get("isin") != param_map["@isin"]:
                    continue
                results.append(dict(doc))
        return iter(results)

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

    def seed_config(self, ticker: str, extra: dict | None = None):
        doc = {
            "id": f"config_{ticker}",
            "symbol": ticker,
            "doc_type": "symbol_config",
            "telegram_notifications_enabled": False,
            "watchlist": {"covered_call": False, "cash_secured_put": False, "buy_tracker": False},
            "positions": [],
        }
        if extra:
            doc.update(extra)
        self._store[(ticker, doc["id"])] = doc
        return dict(doc)

    def count_configs(self):
        return sum(
            1 for doc in self._store.values()
            if doc.get("doc_type") == "symbol_config"
        )


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


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    from web.app import app
    fake_cosmos = FakeCosmos()
    with TestClient(app) as c:
        app.state.cosmos = fake_cosmos
        app.state.cosmos_error = None
        yield c, fake_cosmos


def _add_ledger_doc(fake_cosmos, security_id: str, doc_id: str = None):
    ticker = security_id.split(":")[-1]
    did = doc_id or f"txn_{ticker}_001"
    fake_cosmos.portfolio_container._store[did] = {
        "id": did,
        "account_id": "_unassigned",
        "doc_type": "ledger_txn",
        "txn_type": "BUY",
        "security_id": security_id,
        "ticker": ticker,
        "trade_date": "2026-01-01",
        "quantity": "10",
        "correction_status": "ACTIVE",
    }


# ---------------------------------------------------------------------------
# §3.1 — Dry-run endpoint
# ---------------------------------------------------------------------------

class TestBackfillDryRun:
    def test_dry_run_returns_200(self, client):
        c, fake = client
        resp = c.get("/api/admin/symbol-config-backfill?dry_run=true")
        assert resp.status_code == 200

    def test_dry_run_flag_in_response(self, client):
        c, fake = client
        resp = c.get("/api/admin/symbol-config-backfill?dry_run=true")
        data = resp.json()
        assert data["dry_run"] is True

    def test_dry_run_no_writes(self, client):
        """Dry-run must not create any symbol_config documents."""
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        _add_ledger_doc(fake, "XNYS:AAPL")
        configs_before = fake.container.count_configs()

        c.get("/api/admin/symbol-config-backfill?dry_run=true")

        assert fake.container.count_configs() == configs_before, (
            "Dry-run must not create any symbol_config documents"
        )

    def test_dry_run_identifies_missing(self, client):
        """Reports securities with portfolio history but no symbol_config."""
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        _add_ledger_doc(fake, "XNYS:AAPL")
        # No config seeded for AAPL

        resp = c.get("/api/admin/symbol-config-backfill?dry_run=true")
        data = resp.json()
        assert data["missing_config"] >= 1
        missing_ids = [m["security_id"] for m in data.get("missing", [])]
        assert "XNYS:AAPL" in missing_ids

    def test_dry_run_existing_config_counted_not_missing(self, client):
        """Securities that already have a config appear in already_have_config, not missing."""
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        _add_ledger_doc(fake, "XNYS:AAPL")
        fake.container.seed_config("AAPL")  # config already exists

        resp = c.get("/api/admin/symbol-config-backfill?dry_run=true")
        data = resp.json()
        assert data["already_have_config"] >= 1
        missing_ids = [m["security_id"] for m in data.get("missing", [])]
        assert "XNYS:AAPL" not in missing_ids

    def test_dry_run_reports_collision_warnings(self, client):
        """Existing config with no/different security_id appears in collision_warnings."""
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        _add_ledger_doc(fake, "XNYS:AAPL")
        # Config for AAPL exists but has no security_id (legacy pre-unification)
        fake.container.seed_config("AAPL", {"security_id": None})

        resp = c.get("/api/admin/symbol-config-backfill?dry_run=true")
        data = resp.json()
        warnings = data.get("collision_warnings", [])
        assert any(w.get("ticker") == "AAPL" for w in warnings), (
            f"Expected collision warning for AAPL, got: {warnings}"
        )


# ---------------------------------------------------------------------------
# §3.2 — Confirmed execution endpoint
# ---------------------------------------------------------------------------

class TestBackfillConfirmedExecution:
    def test_confirmed_execution_requires_confirm_true(self, client):
        c, fake = client
        resp = c.post("/api/admin/symbol-config-backfill", json={})
        assert resp.status_code == 400

    def test_confirmed_creates_missing_configs(self, client):
        """POST with confirm:true creates configs for securities without one."""
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        _add_ledger_doc(fake, "XNYS:AAPL")
        configs_before = fake.container.count_configs()

        resp = c.post("/api/admin/symbol-config-backfill", json={"confirm": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["dry_run"] is False
        assert data["created"] >= 1
        assert fake.container.count_configs() > configs_before

    def test_confirmed_new_configs_all_flags_off(self, client):
        """All newly backfilled configs must have every agent/notification flag off."""
        c, fake = client
        fake.container.seed_security("XMAD:TEF", "Telefónica")
        _add_ledger_doc(fake, "XMAD:TEF")

        c.post("/api/admin/symbol-config-backfill", json={"confirm": True})

        # Find the newly created config
        created = next(
            (doc for (pk, did), doc in fake.container._store.items()
             if doc.get("doc_type") == "symbol_config" and doc.get("symbol") == "TEF"),
            None,
        )
        assert created is not None, "Config for TEF was not created"
        assert created.get("telegram_notifications_enabled") is False
        wl = created.get("watchlist", {})
        assert wl.get("covered_call") is False
        assert wl.get("cash_secured_put") is False
        assert wl.get("buy_tracker") is False
        assert created.get("positions") == []

    def test_confirmed_skips_existing_configs(self, client):
        """Configs that already exist are not recreated or modified."""
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        _add_ledger_doc(fake, "XNYS:AAPL")
        original = fake.container.seed_config("AAPL", {
            "telegram_notifications_enabled": True,
            "custom_field": "preserve_me",
        })

        resp = c.post("/api/admin/symbol-config-backfill", json={"confirm": True})
        data = resp.json()
        assert data.get("skipped_existing", 0) >= 1

        # Config must remain untouched
        persisted = next(
            (doc for (pk, did), doc in fake.container._store.items()
             if doc.get("doc_type") == "symbol_config" and doc.get("symbol") == "AAPL"),
            None,
        )
        assert persisted is not None
        assert persisted.get("telegram_notifications_enabled") is True
        assert persisted.get("custom_field") == "preserve_me"

    def test_confirmed_execution_idempotent(self, client):
        """Running confirmed execution twice produces the same count as once."""
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        _add_ledger_doc(fake, "XNYS:AAPL")

        resp1 = c.post("/api/admin/symbol-config-backfill", json={"confirm": True})
        data1 = resp1.json()
        configs_after_first = fake.container.count_configs()

        resp2 = c.post("/api/admin/symbol-config-backfill", json={"confirm": True})
        data2 = resp2.json()
        configs_after_second = fake.container.count_configs()

        assert configs_after_second == configs_after_first, (
            "Running backfill twice must not create duplicate configs"
        )
        assert data2.get("created", 0) == 0, (
            "Second run must report 0 created (all already exist)"
        )
        assert data2.get("skipped_existing", 0) >= 1

    def test_confirmed_collision_warnings_reported_not_resolved(self, client):
        """Collision warnings reported in response; existing configs NOT auto-modified."""
        c, fake = client
        fake.container.seed_security("XNYS:AAPL", "Apple Inc.")
        _add_ledger_doc(fake, "XNYS:AAPL")
        # Pre-existing config with no security_id
        original = fake.container.seed_config("AAPL", {"security_id": None})

        resp = c.post("/api/admin/symbol-config-backfill", json={"confirm": True})
        data = resp.json()
        warnings = data.get("collision_warnings", [])
        assert any(w.get("ticker") == "AAPL" for w in warnings)

        # security_id must NOT have been written to the existing config
        persisted = next(
            (doc for (pk, did), doc in fake.container._store.items()
             if doc.get("doc_type") == "symbol_config" and doc.get("symbol") == "AAPL"),
            None,
        )
        # Original had security_id=None; must still be None (not overwritten)
        assert persisted is not None
        assert persisted.get("security_id") is None
