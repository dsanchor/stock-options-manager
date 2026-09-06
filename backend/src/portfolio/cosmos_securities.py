"""Cosmos operations for security_master documents in the symbols container.

Security master docs co-locate with symbol_config docs in the same partition
(ticker). Existing symbol_config docs are never touched.

Document shape:
  id:           sec_{MIC}_{TICKER}   (colons → underscores)
  symbol:       {TICKER}             (partition key)
  doc_type:     security_master
  security_id:  {MIC}:{TICKER}
  ...
"""

from __future__ import annotations

import logging
import unicodedata
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from azure.cosmos.exceptions import CosmosResourceNotFoundError

logger = logging.getLogger(__name__)

_COSMOS_SYSTEM_KEYS = {"_rid", "_self", "_etag", "_attachments", "_ts"}


def _clean(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k not in _COSMOS_SYSTEM_KEYS}


def security_id_to_doc_id(security_id: str) -> str:
    """'XNYS:AAPL' → 'sec_XNYS_AAPL'"""
    return "sec_" + security_id.replace(":", "_")


def security_id_to_ticker(security_id: str) -> str:
    """'XNYS:AAPL' → 'AAPL'"""
    parts = security_id.split(":", 1)
    return parts[1] if len(parts) == 2 else security_id


def make_security_id(exchange_mic: str, ticker: str) -> str:
    """'XNYS', 'AAPL' → 'XNYS:AAPL'"""
    return f"{exchange_mic.upper()}:{ticker.upper()}"


def _normalize_alias(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


class CosmosSecuritiesService:
    """CRUD for security_master documents in the symbols container."""

    def __init__(self, symbols_container) -> None:
        self.container = symbols_container

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # ── Create ─────────────────────────────────────────────────────────

    def create_security(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new security_master document.

        Args:
            data: Must contain ticker, company_name, exchange_mic.
                  Optional: isin, cusip, sedol, asset_class, listing_currency,
                  broker_ids, aliases.

        Returns:
            Created document (cleaned of Cosmos system keys).

        Raises:
            ValueError with {error: "collision", existing: {...}} detail on
            ISIN or security_id duplicate.
        """
        ticker = str(data["ticker"]).strip().upper()
        mic = str(data["exchange_mic"]).strip().upper()
        security_id = make_security_id(mic, ticker)
        doc_id = security_id_to_doc_id(security_id)

        # Check security_id collision (same doc_id in same partition)
        try:
            existing = self.container.read_item(item=doc_id, partition_key=ticker)
            raise _CollisionError("security_id", _clean(existing))
        except CosmosResourceNotFoundError:
            pass

        # Check ISIN collision (cross-partition query)
        isin = data.get("isin")
        if isin:
            existing_isin = self._find_by_isin(isin)
            if existing_isin:
                raise _CollisionError("isin", _clean(existing_isin))

        now = self._now()
        aliases_raw = data.get("aliases") or []
        # Normalise alias entries
        aliases: List[Dict[str, str]] = []
        for a in aliases_raw:
            if isinstance(a, dict):
                value = str(a.get("value", "")).strip()
                aliases.append({
                    "source": str(a.get("source", "user")),
                    "value": value,
                    "normalized": _normalize_alias(value),
                })

        doc: Dict[str, Any] = {
            "id": doc_id,
            "symbol": ticker,
            "doc_type": "security_master",
            "security_id": security_id,
            "legacy_symbol": ticker,
            "ticker": ticker,
            "company_name": str(data["company_name"]).strip(),
            "exchange_mic": mic,
            "asset_class": str(data.get("asset_class", "Equity")),
            "listing_currency": str(data.get("listing_currency", "USD")).upper(),
            "status": "ACTIVE",
            "aliases": aliases,
            "created_at": now,
            "updated_at": now,
        }
        for optional_field in ("isin", "cusip", "sedol", "broker_ids"):
            if data.get(optional_field) is not None:
                doc[optional_field] = data[optional_field]

        created = self.container.create_item(doc)
        return _clean(created)

    # ── Read ───────────────────────────────────────────────────────────

    def get_security(self, security_id: str) -> Optional[Dict[str, Any]]:
        """Lookup a security_master by MIC:TICKER security_id.

        Returns cleaned doc or None.
        """
        ticker = security_id_to_ticker(security_id)
        doc_id = security_id_to_doc_id(security_id)
        try:
            doc = self.container.read_item(item=doc_id, partition_key=ticker)
            return _clean(doc)
        except CosmosResourceNotFoundError:
            return None

    def list_securities(self) -> List[Dict[str, Any]]:
        """Return all security_master documents (cross-partition)."""
        query = "SELECT * FROM c WHERE c.doc_type = 'security_master'"
        return [
            _clean(d)
            for d in self.container.query_items(
                query=query,
                enable_cross_partition_query=True,
            )
        ]

    def find_candidates_for_name(
        self, normalized_name: str, limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Find security_master docs whose aliases or company_name match.

        Uses simple substring matching on the normalised name.
        Returns list of (security_doc, score) ordered by score descending.
        """
        all_securities = self.list_securities()
        scored = []
        for sec in all_securities:
            score = _score_match(sec, normalized_name)
            if score > 0:
                scored.append((score, sec))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[:limit]]

    # ── Helpers ────────────────────────────────────────────────────────

    def _find_by_isin(self, isin: str) -> Optional[Dict[str, Any]]:
        query = (
            "SELECT * FROM c WHERE c.doc_type = 'security_master' "
            "AND c.isin = @isin"
        )
        results = list(self.container.query_items(
            query=query,
            parameters=[{"name": "@isin", "value": isin}],
            enable_cross_partition_query=True,
        ))
        return results[0] if results else None


class _CollisionError(Exception):
    """Raised on ISIN or security_id collision during create_security."""
    def __init__(self, field: str, existing: Dict[str, Any]) -> None:
        self.field = field
        self.existing = existing
        super().__init__(f"{field} collision: {existing.get('security_id')}")


def _normalize_text(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", str(s))
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def _score_match(sec: Dict[str, Any], query: str) -> float:
    """Compute a match score between a security doc and a normalised query string.

    Returns 0.0 for no match, up to 1.0 for exact match.
    """
    if not query:
        return 0.0
    q = query.lower()

    # Exact company name match
    cname = _normalize_text(sec.get("company_name", ""))
    if cname == q:
        return 1.0

    # Alias exact match
    for alias in sec.get("aliases", []):
        if alias.get("normalized", "").lower() == q:
            return 0.95

    # Partial: query is contained in company name
    if q in cname:
        return 0.8

    # Ticker match
    ticker = _normalize_text(sec.get("ticker", ""))
    if ticker == q:
        return 0.7

    # Partial alias match
    for alias in sec.get("aliases", []):
        norm = alias.get("normalized", "").lower()
        if q in norm or norm in q:
            return 0.5

    return 0.0
