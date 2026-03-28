"""CosmosDB service layer for the stock-options-manager.

Provides all database operations: symbol config CRUD, watchlist queries,
position management, decision/signal write and read, and dashboard queries.

Uses a single container ("symbols") with partition key /symbol and a hybrid
document model (symbol_config, decision, signal doc types).
"""

from azure.cosmos import CosmosClient, PartitionKey
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from typing import Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class CosmosDBService:
    """Service layer for CosmosDB operations."""

    def __init__(self, endpoint: str, key: str,
                 database_name: str = "stock-options-manager") -> None:
        self.client = CosmosClient(endpoint, credential=key)
        self.database = self.client.get_database_client(database_name)
        self.container = self.database.get_container_client("symbols")

    # ── Symbol Config CRUD ─────────────────────────────────────────────

    def create_symbol(self, symbol: str, exchange: str,
                      display_name: str = "",
                      covered_call: bool = False,
                      cash_secured_put: bool = False) -> dict:
        """Create a new symbol config document."""
        now = datetime.utcnow().isoformat() + "Z"
        doc = {
            "id": f"config_{symbol}",
            "symbol": symbol,
            "doc_type": "symbol_config",
            "exchange": exchange,
            "display_name": display_name or symbol,
            "watchlist": {
                "covered_call": covered_call,
                "cash_secured_put": cash_secured_put,
            },
            "positions": [],
            "created_at": now,
            "updated_at": now,
        }
        return self.container.create_item(doc)

    def get_symbol(self, symbol: str) -> Optional[dict]:
        """Get symbol config by ticker."""
        try:
            return self.container.read_item(
                item=f"config_{symbol}",
                partition_key=symbol,
            )
        except CosmosResourceNotFoundError:
            return None

    def list_symbols(self) -> list[dict]:
        """List all symbol configs (cross-partition)."""
        query = "SELECT * FROM c WHERE c.doc_type = 'symbol_config'"
        return list(self.container.query_items(
            query=query,
            enable_cross_partition_query=True,
        ))

    def update_watchlist(self, symbol: str,
                         covered_call: Optional[bool] = None,
                         cash_secured_put: Optional[bool] = None) -> dict:
        """Update watchlist flags for a symbol."""
        doc = self.get_symbol(symbol)
        if doc is None:
            raise ValueError(f"Symbol {symbol} not found")
        if covered_call is not None:
            doc["watchlist"]["covered_call"] = covered_call
        if cash_secured_put is not None:
            doc["watchlist"]["cash_secured_put"] = cash_secured_put
        doc["updated_at"] = datetime.utcnow().isoformat() + "Z"
        return self.container.replace_item(item=doc["id"], body=doc)

    def delete_symbol(self, symbol: str) -> None:
        """Delete a symbol config and ALL associated decisions/signals."""
        try:
            self.container.delete_item(
                item=f"config_{symbol}",
                partition_key=symbol,
            )
        except CosmosResourceNotFoundError:
            pass
        # Delete all decisions and signals in this partition
        query = (
            "SELECT c.id FROM c "
            "WHERE c.symbol = @symbol AND c.doc_type != 'symbol_config'"
        )
        items = list(self.container.query_items(
            query=query,
            parameters=[{"name": "@symbol", "value": symbol}],
            partition_key=symbol,
        ))
        for item in items:
            self.container.delete_item(item=item["id"], partition_key=symbol)

    # ── Watchlist Queries (used by scheduler) ──────────────────────────

    def get_covered_call_symbols(self) -> list[dict]:
        """Get all symbols enabled for covered call watching."""
        query = (
            "SELECT * FROM c WHERE c.doc_type = 'symbol_config' "
            "AND c.watchlist.covered_call = true"
        )
        return list(self.container.query_items(
            query=query,
            enable_cross_partition_query=True,
        ))

    def get_cash_secured_put_symbols(self) -> list[dict]:
        """Get all symbols enabled for cash-secured put watching."""
        query = (
            "SELECT * FROM c WHERE c.doc_type = 'symbol_config' "
            "AND c.watchlist.cash_secured_put = true"
        )
        return list(self.container.query_items(
            query=query,
            enable_cross_partition_query=True,
        ))

    def get_symbols_with_active_positions(self,
                                          position_type: str) -> list[dict]:
        """Get symbol configs that have active positions of a given type.

        Args:
            position_type: "call" or "put"

        Returns:
            List of symbol_config documents with at least one active position
            matching the type. Adds ``_active_positions`` key with the filtered
            list for caller convenience.
        """
        query = (
            "SELECT * FROM c WHERE c.doc_type = 'symbol_config' "
            "AND ARRAY_LENGTH(c.positions) > 0"
        )
        results = list(self.container.query_items(
            query=query,
            enable_cross_partition_query=True,
        ))
        filtered: list[dict] = []
        for doc in results:
            active = [
                p for p in doc.get("positions", [])
                if p["type"] == position_type and p["status"] == "active"
            ]
            if active:
                doc["_active_positions"] = active
                filtered.append(doc)
        return filtered

    # ── Position Management ────────────────────────────────────────────

    def add_position(self, symbol: str, position_type: str,
                     strike: float, expiration: str,
                     notes: str = "") -> dict:
        """Add an open position to a symbol."""
        doc = self.get_symbol(symbol)
        if doc is None:
            raise ValueError(f"Symbol {symbol} not found")

        exp_compact = expiration.replace("-", "")
        position_id = f"pos_{symbol}_{position_type}_{strike}_{exp_compact}"

        existing_ids = {p["position_id"] for p in doc.get("positions", [])}
        if position_id in existing_ids:
            raise ValueError(f"Position {position_id} already exists")

        position = {
            "position_id": position_id,
            "type": position_type,
            "strike": strike,
            "expiration": expiration,
            "opened_at": datetime.utcnow().isoformat() + "Z",
            "status": "active",
            "notes": notes,
        }
        doc["positions"].append(position)
        doc["updated_at"] = datetime.utcnow().isoformat() + "Z"
        return self.container.replace_item(item=doc["id"], body=doc)

    def close_position(self, symbol: str, position_id: str) -> dict:
        """Mark a position as closed."""
        doc = self.get_symbol(symbol)
        if doc is None:
            raise ValueError(f"Symbol {symbol} not found")

        for pos in doc.get("positions", []):
            if pos["position_id"] == position_id:
                pos["status"] = "closed"
                pos["closed_at"] = datetime.utcnow().isoformat() + "Z"
                break
        else:
            raise ValueError(f"Position {position_id} not found")

        doc["updated_at"] = datetime.utcnow().isoformat() + "Z"
        return self.container.replace_item(item=doc["id"], body=doc)

    def delete_position(self, symbol: str, position_id: str) -> dict:
        """Remove a position and all linked decisions/signals from a symbol."""
        doc = self.get_symbol(symbol)
        if doc is None:
            raise ValueError(f"Symbol {symbol} not found")

        doc["positions"] = [
            p for p in doc.get("positions", [])
            if p["position_id"] != position_id
        ]
        doc["updated_at"] = datetime.utcnow().isoformat() + "Z"
        result = self.container.replace_item(item=doc["id"], body=doc)

        # Cascade: delete all decisions linked to this position
        dec_query = (
            "SELECT c.id FROM c "
            "WHERE c.doc_type = 'decision' AND c.position_id = @position_id"
        )
        decisions = list(self.container.query_items(
            query=dec_query,
            parameters=[{"name": "@position_id", "value": position_id}],
            partition_key=symbol,
        ))
        decision_ids = {d["id"] for d in decisions}

        # Cascade: delete all signals linked to those decisions
        if decision_ids:
            # CosmosDB doesn't support parameterised IN lists directly,
            # so build a safe literal list from the known document ids.
            id_list = ", ".join(f"'{did}'" for did in decision_ids)
            sig_query = (
                f"SELECT c.id FROM c "
                f"WHERE c.doc_type = 'signal' "
                f"AND c.decision_id IN ({id_list})"
            )
            signals = list(self.container.query_items(
                query=sig_query,
                parameters=[],
                partition_key=symbol,
            ))
            for sig in signals:
                self.container.delete_item(
                    item=sig["id"], partition_key=symbol)

        for dec in decisions:
            self.container.delete_item(item=dec["id"], partition_key=symbol)

        logger.info(
            "Cascade-deleted position %s: %d decisions, %d signals removed",
            position_id, len(decisions),
            len(signals) if decision_ids else 0,
        )
        return result

    def delete_decisions_by_agent_type(
        self, symbol: str, agent_type: str
    ) -> tuple[int, int]:
        """Cascade-delete all decisions (and their signals) for a given agent type on a symbol."""
        dec_query = (
            "SELECT c.id FROM c "
            "WHERE c.doc_type = 'decision' AND c.agent_type = @agent_type"
        )
        decisions = list(self.container.query_items(
            query=dec_query,
            parameters=[{"name": "@agent_type", "value": agent_type}],
            partition_key=symbol,
        ))
        decision_ids = {d["id"] for d in decisions}

        sig_count = 0
        if decision_ids:
            id_list = ", ".join(f"'{did}'" for did in decision_ids)
            sig_query = (
                f"SELECT c.id FROM c "
                f"WHERE c.doc_type = 'signal' "
                f"AND c.decision_id IN ({id_list})"
            )
            signals = list(self.container.query_items(
                query=sig_query,
                parameters=[],
                partition_key=symbol,
            ))
            for sig in signals:
                self.container.delete_item(
                    item=sig["id"], partition_key=symbol)
            sig_count = len(signals)

        for dec in decisions:
            self.container.delete_item(item=dec["id"], partition_key=symbol)

        logger.info(
            "Cascade-deleted agent_type '%s' for %s: %d decisions, %d signals removed",
            agent_type, symbol, len(decisions), sig_count,
        )
        return len(decisions), sig_count

    # ── Decision / Signal Write ────────────────────────────────────────

    def write_decision(self, symbol: str, agent_type: str,
                       decision_data: dict,
                       timestamp: str | None = None,
                       ttl_seconds: int | None = None) -> dict:
        """Write a decision document.

        Args:
            symbol: Ticker symbol (partition key).
            agent_type: One of "covered_call", "cash_secured_put",
                "open_call_monitor", "open_put_monitor".
            decision_data: Full decision dict from agent output.
            timestamp: Override timestamp (ISO format). Defaults to now.
            ttl_seconds: Optional TTL in seconds for automatic expiry.

        Returns:
            The created CosmosDB document.
        """
        ts = timestamp or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        ts_compact = ts.replace("-", "").replace(":", "").replace("T", "_")[:15]

        position_id = decision_data.get("position_id", "")
        id_suffix = f"_{position_id}" if position_id else ""

        doc_id = f"dec_{symbol}_{agent_type}{id_suffix}_{ts_compact}"

        doc: dict = {
            "id": doc_id,
            "symbol": symbol,
            "doc_type": "decision",
            "agent_type": agent_type,
            "timestamp": ts,
            "is_signal": False,
            **decision_data,
        }
        # Ensure the computed id is not overridden by decision_data
        doc["id"] = doc_id

        if ttl_seconds is not None:
            doc["ttl"] = ttl_seconds

        return self.container.create_item(doc)

    def write_signal(self, symbol: str, agent_type: str,
                     signal_data: dict, decision_id: str,
                     timestamp: str | None = None) -> dict:
        """Write a signal document linked to a decision."""
        ts = timestamp or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        ts_compact = ts.replace("-", "").replace(":", "").replace("T", "_")[:15]

        doc_id = f"sig_{symbol}_{agent_type}_{ts_compact}"

        doc: dict = {
            "id": doc_id,
            "symbol": symbol,
            "doc_type": "signal",
            "agent_type": agent_type,
            "timestamp": ts,
            "decision_id": decision_id,
            **signal_data,
        }
        doc["id"] = doc_id

        return self.container.create_item(doc)

    # ── Decision / Signal Read (context injection) ─────────────────────

    def get_recent_decisions(self, symbol: str, agent_type: str,
                             max_entries: int = 20,
                             position_id: str | None = None) -> list[dict]:
        """Get recent decisions for a symbol+agent, newest first.

        For position monitors, optionally filter by position_id.
        """
        conditions = [
            "c.doc_type = 'decision'",
            "c.agent_type = @agent_type",
        ]
        params: list[dict] = [
            {"name": "@agent_type", "value": agent_type},
        ]
        if position_id:
            conditions.append("c.position_id = @position_id")
            params.append({"name": "@position_id", "value": position_id})

        query = (
            f"SELECT TOP @limit * FROM c WHERE {' AND '.join(conditions)} "
            "ORDER BY c.timestamp DESC"
        )
        params.append({"name": "@limit", "value": max_entries})

        return list(self.container.query_items(
            query=query,
            parameters=params,
            partition_key=symbol,
        ))

    def get_recent_signals(self, symbol: str, agent_type: str,
                           max_entries: int = 10) -> list[dict]:
        """Get recent signals for a symbol+agent, newest first."""
        query = (
            "SELECT TOP @limit * FROM c "
            "WHERE c.doc_type = 'signal' AND c.agent_type = @agent_type "
            "ORDER BY c.timestamp DESC"
        )
        return list(self.container.query_items(
            query=query,
            parameters=[
                {"name": "@agent_type", "value": agent_type},
                {"name": "@limit", "value": max_entries},
            ],
            partition_key=symbol,
        ))

    # ── Single-Document Lookups ────────────────────────────────────────

    def get_decision_by_id(self, decision_id: str) -> dict | None:
        """Get a single decision by its document ID (cross-partition)."""
        query = "SELECT * FROM c WHERE c.id = @id AND c.doc_type = 'decision'"
        results = list(self.container.query_items(
            query=query,
            parameters=[{"name": "@id", "value": decision_id}],
            enable_cross_partition_query=True,
        ))
        return results[0] if results else None

    # ── Dashboard Queries ──────────────────────────────────────────────

    def get_all_signals(self, agent_type: str | None = None,
                        since: str | None = None,
                        limit: int = 100) -> list[dict]:
        """Get signals across all symbols (cross-partition query)."""
        conditions = ["c.doc_type = 'signal'"]
        params: list[dict] = []
        if agent_type:
            conditions.append("c.agent_type = @agent_type")
            params.append({"name": "@agent_type", "value": agent_type})
        if since:
            conditions.append("c.timestamp >= @since")
            params.append({"name": "@since", "value": since})

        query = (
            f"SELECT TOP @limit * FROM c WHERE {' AND '.join(conditions)} "
            "ORDER BY c.timestamp DESC"
        )
        params.append({"name": "@limit", "value": limit})

        return list(self.container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True,
        ))

    def get_all_decisions(self, agent_type: str | None = None,
                          since: str | None = None,
                          limit: int = 100) -> list[dict]:
        """Get decisions across all symbols (cross-partition query)."""
        conditions = ["c.doc_type = 'decision'"]
        params: list[dict] = []
        if agent_type:
            conditions.append("c.agent_type = @agent_type")
            params.append({"name": "@agent_type", "value": agent_type})
        if since:
            conditions.append("c.timestamp >= @since")
            params.append({"name": "@since", "value": since})

        query = (
            f"SELECT TOP @limit * FROM c WHERE {' AND '.join(conditions)} "
            "ORDER BY c.timestamp DESC"
        )
        params.append({"name": "@limit", "value": limit})

        return list(self.container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True,
        ))

    def count_signals_by_symbol(self, agent_type: str,
                                since: str | None = None) -> dict[str, int]:
        """Count signals per symbol for dashboard aggregation."""
        conditions = ["c.doc_type = 'signal'", "c.agent_type = @agent_type"]
        params: list[dict] = [{"name": "@agent_type", "value": agent_type}]
        if since:
            conditions.append("c.timestamp >= @since")
            params.append({"name": "@since", "value": since})

        query = (
            f"SELECT c.symbol, COUNT(1) as count FROM c "
            f"WHERE {' AND '.join(conditions)} GROUP BY c.symbol"
        )
        results = list(self.container.query_items(
            query=query,
            parameters=params,
            enable_cross_partition_query=True,
        ))
        return {r["symbol"]: r["count"] for r in results}
