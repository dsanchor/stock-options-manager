"""Phase 2 regression tests — shared fake Cosmos containers.

All Phase 2 endpoint tests import from this module to avoid duplication.
"""

from __future__ import annotations

from azure.cosmos.exceptions import CosmosResourceNotFoundError, CosmosHttpResponseError


class FakePortfolioContainer:
    """Fake portfolio container supporting keyword-arg calls matching Cosmos SDK."""

    def __init__(self):
        self._store: dict = {}

    def read(self):
        return {}

    def upsert_item(self, body):
        self._store[body["id"]] = dict(body)
        return dict(body)

    def create_item(self, body):
        if body["id"] in self._store:
            # Simulate Cosmos 409 Conflict; message includes "409" for detection
            raise CosmosHttpResponseError(
                message="409 Conflict — Entity with specified id already exists in the system.",
                response=None,
            )
        self._store[body["id"]] = dict(body)
        return dict(body)

    def read_item(self, item=None, partition_key=None, **kw):
        # Accept both positional and keyword arguments
        key = item
        if key is None and kw:
            key = list(kw.values())[0]
        if key not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        doc = self._store[key]
        if partition_key is not None and doc.get("account_id") != partition_key:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        return dict(doc)

    def replace_item(self, item, body, **kw):
        self._store[item] = dict(body)
        return dict(body)

    def delete_item(self, item=None, partition_key=None, **kw):
        if item in self._store:
            del self._store[item]

    def query_items(self, query="", parameters=None, enable_cross_partition_query=True,
                    partition_key=None):
        param_map = {p["name"]: p["value"] for p in (parameters or [])}
        results = list(self._store.values())
        # Filter deleted
        if "NOT IS_DEFINED(c.deleted_at)" in query:
            results = [d for d in results if "deleted_at" not in d]
        # Active correction_status filter
        if "(NOT IS_DEFINED(c.correction_status) OR c.correction_status = 'ACTIVE')" in query:
            results = [
                d for d in results
                if d.get("correction_status", "ACTIVE") == "ACTIVE"
            ]
        # doc_type filter
        if "doc_type = 'account'" in query:
            results = [d for d in results if d.get("doc_type") == "account"]
        if "doc_type = 'ledger_txn'" in query:
            results = [d for d in results if d.get("doc_type") == "ledger_txn"]
        # account_id filter (by param or partition_key)
        if "@account_id" in param_map:
            results = [d for d in results if d.get("account_id") == param_map["@account_id"]]
        if partition_key is not None:
            results = [d for d in results if d.get("account_id") == partition_key]
        # security_id filter
        if "@security_id" in param_map:
            results = [d for d in results if d.get("security_id") == param_map["@security_id"]]
        # ca_group_id filter (Amendment H)
        if "@ca_group_id" in param_map:
            results = [d for d in results if d.get("ca_group_id") == param_map["@ca_group_id"]]
        # date filter
        if "@as_of_date" in param_map:
            results = [d for d in results if d.get("trade_date", "") <= param_map["@as_of_date"]]
        if "@date_from" in param_map:
            results = [d for d in results if d.get("trade_date", "") >= param_map["@date_from"]]
        if "@date_to" in param_map:
            results = [d for d in results if d.get("trade_date", "") <= param_map["@date_to"]]
        if "COUNT" in query:
            return iter([len(results)])
        return iter(results)


class FakeImportSessionsContainer:
    def __init__(self):
        self._store: dict = {}

    def read(self):
        return {}

    def create_item(self, body):
        self._store[body["id"]] = dict(body)
        return dict(body)

    def read_item(self, item=None, partition_key=None, **kw):
        key = item
        if key not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        return dict(self._store[key])

    def replace_item(self, item, body, **kw):
        self._store[item] = dict(body)
        return dict(body)

    def query_items(self, **kw):
        return iter([])


class FakeSymbolsContainer:
    def __init__(self):
        self._store: dict = {}

    def read(self):
        return {}

    def read_item(self, item=None, partition_key=None, **kw):
        key = (partition_key, item)
        if key not in self._store:
            raise CosmosResourceNotFoundError(message="not found", response=None)
        return dict(self._store[key])

    def create_item(self, body):
        ticker = body.get("symbol", body.get("ticker", ""))
        key = (ticker, body["id"])
        self._store[key] = dict(body)
        return dict(body)

    def query_items(self, query="", parameters=None, enable_cross_partition_query=False,
                    partition_key=None):
        param_map = {p["name"]: p["value"] for p in (parameters or [])}
        results = [v for v in self._store.values() if v.get("doc_type") == "security_master"]
        if "@isin" in param_map:
            results = [r for r in results if r.get("isin") == param_map["@isin"]]
        return iter(results)

    def replace_item(self, item, body, **kw):
        for key in self._store:
            if self._store[key].get("id") == item:
                self._store[key] = dict(body)
                return dict(body)
        raise CosmosResourceNotFoundError(message="not found", response=None)

    def upsert_item(self, body):
        ticker = body.get("symbol", body.get("ticker", ""))
        key = (ticker, body["id"])
        self._store[key] = dict(body)
        return dict(body)


class FakeCosmos:
    def __init__(self):
        self.container = FakeSymbolsContainer()
        self.portfolio_container = FakePortfolioContainer()
        self.import_sessions_container = FakeImportSessionsContainer()

    def list_symbols(self):
        return []

    def get_symbol(self, symbol):
        return None
