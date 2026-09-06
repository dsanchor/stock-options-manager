"""Cosmos operations for portfolio and import_sessions containers.

portfolio container:
  - Partition key: /account_id
  - Doc types: ledger_txn, import_batch

import_sessions container:
  - Partition key: /session_id
  - Doc types: import_session (7-day TTL)

Both containers are best-effort at startup (following repository convention).
When unavailable, operations raise StorageUnavailableError so the router can
return HTTP 503 with the frozen error shape.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from azure.cosmos.exceptions import CosmosResourceNotFoundError

logger = logging.getLogger(__name__)

_COSMOS_SYSTEM_KEYS = {"_rid", "_self", "_etag", "_attachments", "_ts"}

# 7-day TTL for import sessions (in seconds)
_SESSION_TTL_SECONDS = 7 * 24 * 3600


class StorageUnavailableError(Exception):
    """Raised when a required Cosmos container is not available."""


def _clean(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k not in _COSMOS_SYSTEM_KEYS}


class CosmosPortfolioService:
    """Portfolio and import-sessions Cosmos operations."""

    def __init__(self, portfolio_container, import_sessions_container) -> None:
        self.portfolio_container = portfolio_container
        self.import_sessions_container = import_sessions_container

    @property
    def portfolio_available(self) -> bool:
        return self.portfolio_container is not None

    @property
    def sessions_available(self) -> bool:
        return self.import_sessions_container is not None

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _require_sessions(self) -> None:
        if not self.sessions_available:
            raise StorageUnavailableError(
                "import_sessions container not configured — "
                "run scripts/provision_cosmosdb.sh"
            )

    def _require_portfolio(self) -> None:
        if not self.portfolio_available:
            raise StorageUnavailableError(
                "portfolio container not configured — "
                "run scripts/provision_cosmosdb.sh"
            )

    # ── Import Sessions ─────────────────────────────────────────────────

    def create_session(self, session_doc: Dict[str, Any]) -> Dict[str, Any]:
        """Write a new import_session document. Raises StorageUnavailableError."""
        self._require_sessions()
        created = self.import_sessions_container.create_item(session_doc)
        return _clean(created)

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Read an import_session by session_id. Returns None if not found."""
        self._require_sessions()
        try:
            doc = self.import_sessions_container.read_item(
                item=session_id, partition_key=session_id
            )
            return _clean(doc)
        except CosmosResourceNotFoundError:
            return None

    def update_session(self, session_doc: Dict[str, Any]) -> Dict[str, Any]:
        """Replace an existing import_session document."""
        self._require_sessions()
        session_doc["updated_at"] = self._now()
        updated = self.import_sessions_container.replace_item(
            item=session_doc["id"], body=session_doc
        )
        return _clean(updated)

    # ── Ledger (portfolio container) ─────────────────────────────────────

    def write_ledger_txn(self, txn_doc: Dict[str, Any]) -> Dict[str, Any]:
        """Upsert a ledger_txn document into the portfolio container."""
        self._require_portfolio()
        created = self.portfolio_container.upsert_item(txn_doc)
        return _clean(created)

    def get_movements(
        self,
        account_id: Optional[str] = None,
        security_id: Optional[str] = None,
        txn_type: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[List[Dict[str, Any]], int]:
        """Return paginated ledger_txn documents.

        Returns (items, total_count). Excludes soft-deleted records.
        """
        self._require_portfolio()

        conditions = [
            "c.doc_type = 'ledger_txn'",
            "NOT IS_DEFINED(c.deleted_at)",
        ]
        params: List[Dict[str, Any]] = []

        if account_id:
            conditions.append("c.account_id = @account_id")
            params.append({"name": "@account_id", "value": account_id})
        if security_id:
            conditions.append("c.security_id = @security_id")
            params.append({"name": "@security_id", "value": security_id})
        if txn_type:
            conditions.append("c.txn_type = @txn_type")
            params.append({"name": "@txn_type", "value": txn_type})
        if date_from:
            conditions.append("c.trade_date >= @date_from")
            params.append({"name": "@date_from", "value": date_from})
        if date_to:
            conditions.append("c.trade_date <= @date_to")
            params.append({"name": "@date_to", "value": date_to})

        where = " AND ".join(conditions)
        count_query = f"SELECT VALUE COUNT(1) FROM c WHERE {where}"
        data_query = f"SELECT * FROM c WHERE {where} ORDER BY c.trade_date DESC"

        query_kwargs: Dict[str, Any] = {
            "enable_cross_partition_query": True,
        }
        if account_id:
            # Single-partition query when account is known
            query_kwargs = {"partition_key": account_id}

        try:
            count_result = list(self.portfolio_container.query_items(
                query=count_query,
                parameters=params,
                enable_cross_partition_query=True,
            ))
            total = count_result[0] if count_result else 0
        except Exception as exc:
            logger.warning("Count query failed: %s", exc)
            total = 0

        try:
            all_items = list(self.portfolio_container.query_items(
                query=data_query,
                parameters=params,
                enable_cross_partition_query=True,
            ))
            items = all_items[offset: offset + limit]
        except Exception as exc:
            logger.warning("Data query failed: %s", exc)
            items = []
            total = 0

        return [_clean(d) for d in items], total

    def get_all_movements_for_holdings(self) -> List[Dict[str, Any]]:
        """Return all non-deleted ledger_txn docs (for holdings computation)."""
        self._require_portfolio()
        query = (
            "SELECT * FROM c WHERE c.doc_type = 'ledger_txn' "
            "AND NOT IS_DEFINED(c.deleted_at)"
        )
        try:
            items = list(self.portfolio_container.query_items(
                query=query,
                enable_cross_partition_query=True,
            ))
            return [_clean(d) for d in items]
        except Exception as exc:
            logger.warning("get_all_movements_for_holdings failed: %s", exc)
            return []

    def soft_delete_movement(self, movement_id: str, account_id: str) -> Optional[Dict[str, Any]]:
        """Soft-delete a movement by setting deleted_at.

        Returns the updated doc, or None if not found.
        """
        self._require_portfolio()
        try:
            doc = self.portfolio_container.read_item(
                item=movement_id, partition_key=account_id
            )
        except CosmosResourceNotFoundError:
            return None

        doc["deleted_at"] = self._now()
        updated = self.portfolio_container.replace_item(item=doc["id"], body=doc)
        return _clean(updated)

    def find_probable_duplicate(
        self,
        security_id: str,
        txn_type: str,
        trade_date: str,
        quantity: str,
        gross_eur: str,
    ) -> Optional[Dict[str, Any]]:
        """Check for an existing committed movement with matching fingerprint."""
        self._require_portfolio()
        query = (
            "SELECT TOP 1 * FROM c WHERE c.doc_type = 'ledger_txn' "
            "AND NOT IS_DEFINED(c.deleted_at) "
            "AND c.security_id = @security_id "
            "AND c.txn_type = @txn_type "
            "AND c.trade_date = @trade_date "
            "AND c.quantity = @quantity"
        )
        params = [
            {"name": "@security_id", "value": security_id},
            {"name": "@txn_type", "value": txn_type},
            {"name": "@trade_date", "value": trade_date},
            {"name": "@quantity", "value": quantity},
        ]
        try:
            results = list(self.portfolio_container.query_items(
                query=query,
                parameters=params,
                enable_cross_partition_query=True,
            ))
            return _clean(results[0]) if results else None
        except Exception as exc:
            logger.warning("find_probable_duplicate failed: %s", exc)
            return None
