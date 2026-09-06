"""Cosmos operations for portfolio and import_sessions containers.

portfolio container:
  - Partition key: /account_id
  - Doc types: ledger_txn, account, import_batch

import_sessions container:
  - Partition key: /session_id
  - Doc types: import_session (7-day TTL)

Both containers are best-effort at startup (following repository convention).
When unavailable, operations raise StorageUnavailableError so the router can
return HTTP 503 with the frozen error shape.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional
from uuid import uuid4

from azure.cosmos.exceptions import CosmosResourceNotFoundError

logger = logging.getLogger(__name__)

_COSMOS_SYSTEM_KEYS = {"_rid", "_self", "_etag", "_attachments", "_ts"}

# 7-day TTL for import sessions (in seconds)
_SESSION_TTL_SECONDS = 7 * 24 * 3600

# Correction statuses excluded from active holdings
_EXCLUDED_CORRECTION_STATUSES = {"SUPERSEDED", "VOIDED"}

# Transfer txn_types
_TRANSFER_TYPES = {"TRANSFER_OUT", "TRANSFER_IN"}


class StorageUnavailableError(Exception):
    """Raised when a required Cosmos container is not available."""


class InsufficientSharesError(Exception):
    """Raised when a transfer source account lacks sufficient shares."""
    def __init__(self, available: str, requested: str) -> None:
        self.available = available
        self.requested = requested
        super().__init__(f"Insufficient shares: available={available}, requested={requested}")


def _clean(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k not in _COSMOS_SYSTEM_KEYS}


def _slugify(text: str) -> str:
    """Convert text to a URL-safe slug."""
    nfkd = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in nfkd if not unicodedata.combining(c))
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def _d(v: Any) -> Decimal:
    if isinstance(v, Decimal):
        return v
    if v is None:
        return Decimal("0")
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal("0")


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

    # ── Accounts ─────────────────────────────────────────────────────────

    def create_account(self, broker: str, name: str, currency: str = "EUR",
                       description: Optional[str] = None) -> Dict[str, Any]:
        """Create a broker account document in the portfolio container.

        The document ID and account_id are a stable slug derived from broker+name.
        Raises StorageUnavailableError or ValueError on duplicate.
        """
        self._require_portfolio()
        account_id = f"acct_{_slugify(broker)}_{_slugify(name)}"
        now = self._now()
        doc: Dict[str, Any] = {
            "id": account_id,
            "account_id": account_id,
            "doc_type": "account",
            "broker": broker,
            "name": name,
            "currency": currency.upper(),
            "created_at": now,
            "updated_at": now,
        }
        if description:
            doc["description"] = description

        try:
            # Use create_item to fail on duplicate
            created = self.portfolio_container.create_item(doc)
            return _clean(created)
        except Exception as exc:
            # Cosmos raises CosmosHttpResponseError (status 409) on duplicate id
            err_str = str(exc)
            if "409" in err_str or "Conflict" in err_str:
                raise ValueError(f"Account {account_id!r} already exists")
            raise

    def get_account(self, account_id: str) -> Optional[Dict[str, Any]]:
        """Read an account document by account_id."""
        self._require_portfolio()
        try:
            doc = self.portfolio_container.read_item(
                item=account_id, partition_key=account_id
            )
            if doc.get("doc_type") != "account":
                return None
            return _clean(doc)
        except CosmosResourceNotFoundError:
            return None

    def list_accounts(self) -> List[Dict[str, Any]]:
        """Return all account documents."""
        self._require_portfolio()
        query = "SELECT * FROM c WHERE c.doc_type = 'account'"
        try:
            items = list(self.portfolio_container.query_items(
                query=query,
                enable_cross_partition_query=True,
            ))
            return [_clean(d) for d in items]
        except Exception as exc:
            logger.warning("list_accounts failed: %s", exc)
            return []

    def update_account(self, account_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update mutable fields on an account document.

        account_id is immutable — the doc's id/account_id are never changed here.
        Returns the updated doc, or None if not found or soft-deleted.
        """
        self._require_portfolio()
        try:
            doc = self.portfolio_container.read_item(
                item=account_id, partition_key=account_id
            )
        except CosmosResourceNotFoundError:
            return None

        if doc.get("doc_type") != "account":
            return None
        if doc.get("deleted_at"):
            return None

        for key, value in updates.items():
            if value is None:
                doc.pop(key, None)
            else:
                doc[key] = value
        doc["updated_at"] = self._now()
        updated = self.portfolio_container.replace_item(item=doc["id"], body=doc)
        return _clean(updated)

    def delete_account(self, account_id: str) -> Optional[Dict[str, Any]]:
        """Soft-delete an account. Blocks if the account has active movements.

        Returns the soft-deleted doc, or None if not found.
        Raises ValueError with movement_count if movements exist.
        """
        self._require_portfolio()
        try:
            doc = self.portfolio_container.read_item(
                item=account_id, partition_key=account_id
            )
        except CosmosResourceNotFoundError:
            return None

        if doc.get("doc_type") != "account":
            return None

        # Count active (non-deleted, non-superseded) movements in this account
        active_count = self._count_active_movements(account_id)
        if active_count > 0:
            raise ValueError(f"account_has_movements:{active_count}")

        doc["deleted_at"] = self._now()
        updated = self.portfolio_container.replace_item(item=doc["id"], body=doc)
        return _clean(updated)

    def _count_active_movements(self, account_id: str) -> int:
        """Count active (non-deleted, non-superseded) ledger_txn docs for an account."""
        query = (
            "SELECT VALUE COUNT(1) FROM c "
            "WHERE c.doc_type = 'ledger_txn' "
            "AND NOT IS_DEFINED(c.deleted_at) "
            "AND (NOT IS_DEFINED(c.correction_status) OR c.correction_status = 'ACTIVE') "
            "AND c.account_id = @account_id"
        )
        params = [{"name": "@account_id", "value": account_id}]
        try:
            result = list(self.portfolio_container.query_items(
                query=query,
                parameters=params,
                partition_key=account_id,
            ))
            return result[0] if result else 0
        except Exception as exc:
            logger.warning("_count_active_movements failed: %s", exc)
            return 0

    # ── Ledger (portfolio container) ─────────────────────────────────────

    def write_ledger_txn(self, txn_doc: Dict[str, Any]) -> Dict[str, Any]:
        """Upsert a ledger_txn document into the portfolio container."""
        self._require_portfolio()
        created = self.portfolio_container.upsert_item(txn_doc)
        return _clean(created)

    def get_movement(self, movement_id: str, account_id: str) -> Optional[Dict[str, Any]]:
        """Read a single ledger_txn by id + account_id (partition key)."""
        self._require_portfolio()
        try:
            doc = self.portfolio_container.read_item(
                item=movement_id, partition_key=account_id
            )
            if doc.get("doc_type") != "ledger_txn":
                return None
            return _clean(doc)
        except CosmosResourceNotFoundError:
            return None

    def create_manual_movement(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a manual ledger_txn document.

        Generates a unique ID (mvt_{uuid4}), sets import_source='manual',
        computes net, and stores the doc.
        Raises ValueError for business rule violations.
        """
        self._require_portfolio()
        txn_type = data.get("txn_type", "")
        security_id = data.get("security_id", "")
        trade_date = data.get("trade_date", "")
        account_id = data.get("account_id", "_unassigned")

        if not security_id:
            raise ValueError("security_id is required")
        if not trade_date:
            raise ValueError("trade_date is required")
        if txn_type not in {"BUY", "SELL", "DIVIDEND"}:
            raise ValueError(f"txn_type must be BUY, SELL, or DIVIDEND for manual creation; got {txn_type!r}")

        # DERECHOS: validate quantity=0 semantics
        sales_type = data.get("sales_type")
        if txn_type == "SELL" and not sales_type:
            sales_type = "ACCIONES"
        if txn_type == "SELL" and sales_type not in ("ACCIONES", "DERECHOS"):
            raise ValueError("sales_type must be ACCIONES or DERECHOS")

        gross = data.get("gross") or {}
        fees = data.get("fees") or {}
        wht = data.get("withholding")

        gross_eur = _d(gross.get("eur_amount", "0"))
        fees_eur = _d(fees.get("total_eur", "0"))

        # Withholding
        wht_source_eur = Decimal("0")
        wht_dest_eur = Decimal("0")
        if isinstance(wht, dict):
            src = wht.get("source") or {}
            dst = wht.get("destination") or {}
            wht_source_eur = _d(src.get("amount_eur", "0"))
            wht_dest_eur = _d(dst.get("amount_eur", "0"))

        # net = gross - fees - withholding_source - withholding_dest (all EUR)
        net_eur = gross_eur - fees_eur - wht_source_eur - wht_dest_eur
        currency = gross.get("currency", "EUR").upper()
        net_in_currency = (
            _d(gross.get("amount", "0")) - _d(fees.get("total", "0"))
            - wht_source_eur - wht_dest_eur
        ) if currency == "EUR" else net_eur

        now = self._now()
        movement_id = f"mvt_{uuid4().hex}"
        ticker = security_id.split(":")[-1] if ":" in security_id else security_id

        doc: Dict[str, Any] = {
            "id": movement_id,
            "account_id": account_id,
            "doc_type": "ledger_txn",
            "txn_type": txn_type,
            "security_id": security_id,
            "ticker": ticker,
            "trade_date": trade_date,
            "quantity": str(data.get("quantity", "0")),
            "gross": {
                "amount": str(gross.get("amount", "0")),
                "currency": currency,
                "eur_amount": str(gross_eur),
            },
            "fees": {
                "total": str(fees.get("total", "0")),
                "currency": fees.get("currency", currency),
                "total_eur": str(fees_eur),
            },
            "net": {
                "amount": str(net_in_currency.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)),
                "currency": currency,
                "eur_amount": str(net_eur.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)),
            },
            "import_source": "manual",
            "correction_status": "ACTIVE",
            "created_at": now,
            "updated_at": now,
        }

        # FX info
        fx = data.get("fx")
        if fx:
            doc["fx"] = fx
        else:
            doc["fx"] = {"rate": "1.000000000", "rate_source": "ECB"}

        if wht:
            doc["withholding"] = wht

        if txn_type == "SELL":
            doc["sales_type"] = sales_type

        cost_basis_status = data.get("cost_basis_status")
        if txn_type == "BUY" and cost_basis_status:
            doc["cost_basis_status"] = cost_basis_status
        elif txn_type == "BUY":
            doc["cost_basis_status"] = "COMPLETE"

        if data.get("notes"):
            doc["notes"] = data["notes"]

        created = self.portfolio_container.upsert_item(doc)
        return _clean(created)

    def correct_movement(
        self, movement_id: str, account_id: str, correction_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Replace a movement via the correction workflow.

        Creates a new active doc linked to the original; marks original SUPERSEDED.
        Returns {"original": ..., "replacement": ...}.
        Raises ValueError if already superseded/voided.
        """
        self._require_portfolio()

        original = self.get_movement(movement_id, account_id)
        if original is None:
            raise LookupError(f"Movement {movement_id} not found in account {account_id}")

        if original.get("correction_status") in ("SUPERSEDED", "VOIDED"):
            raise ValueError(f"already_superseded: movement {movement_id} is already {original['correction_status']}")

        correction_note = correction_data.get("correction_note", "").strip()
        if not correction_note:
            raise ValueError("correction_note is required")

        now = self._now()
        new_id = f"mvt_{uuid4().hex}"

        # Build replacement doc: start from original, apply overrides
        replacement = dict(original)
        replacement["id"] = new_id
        replacement["account_id"] = account_id
        replacement["correction_status"] = "ACTIVE"
        replacement["corrects_movement_id"] = movement_id
        replacement["correction_note"] = correction_note
        replacement["import_source"] = "manual"
        replacement["created_at"] = now
        replacement["updated_at"] = now
        # Remove superseded_by if carried over
        replacement.pop("superseded_by", None)

        # Apply field overrides from correction_data
        overridable = ("trade_date", "quantity", "gross", "fees", "withholding",
                       "fx", "sales_type", "cost_basis_status", "notes")
        for field in overridable:
            if correction_data.get(field) is not None:
                replacement[field] = correction_data[field]

        # Recompute net if gross/fees changed
        if "gross" in correction_data or "fees" in correction_data or "withholding" in correction_data:
            gross = replacement.get("gross") or {}
            fees = replacement.get("fees") or {}
            wht = replacement.get("withholding")
            gross_eur = _d(gross.get("eur_amount", "0"))
            fees_eur = _d(fees.get("total_eur", "0"))
            wht_s = Decimal("0")
            wht_d = Decimal("0")
            if isinstance(wht, dict):
                wht_s = _d((wht.get("source") or {}).get("amount_eur", "0"))
                wht_d = _d((wht.get("destination") or {}).get("amount_eur", "0"))
            net_eur = gross_eur - fees_eur - wht_s - wht_d
            currency = gross.get("currency", "EUR").upper()
            replacement["net"] = {
                "amount": str(net_eur.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)),
                "currency": currency,
                "eur_amount": str(net_eur.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)),
            }

        # Write replacement
        self.portfolio_container.upsert_item(replacement)

        # Mark original SUPERSEDED
        orig_doc = self.portfolio_container.read_item(item=movement_id, partition_key=account_id)
        orig_doc["correction_status"] = "SUPERSEDED"
        orig_doc["superseded_by"] = new_id
        orig_doc["updated_at"] = now
        self.portfolio_container.replace_item(item=movement_id, body=orig_doc)

        return {
            "original": _clean(orig_doc),
            "replacement": _clean(replacement),
        }

    def get_movements(
        self,
        account_id: Optional[str] = None,
        security_id: Optional[str] = None,
        txn_type: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        include_superseded: bool = False,
    ) -> tuple[List[Dict[str, Any]], int]:
        """Return paginated ledger_txn documents.

        Returns (items, total_count). Excludes soft-deleted records.
        By default also excludes SUPERSEDED and VOIDED records.
        """
        self._require_portfolio()

        conditions = [
            "c.doc_type = 'ledger_txn'",
            "NOT IS_DEFINED(c.deleted_at)",
        ]
        if not include_superseded:
            conditions.append(
                "(NOT IS_DEFINED(c.correction_status) OR c.correction_status = 'ACTIVE')"
            )
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
        """Return all active (non-deleted, non-superseded) ledger_txn docs."""
        self._require_portfolio()
        query = (
            "SELECT * FROM c WHERE c.doc_type = 'ledger_txn' "
            "AND NOT IS_DEFINED(c.deleted_at) "
            "AND (NOT IS_DEFINED(c.correction_status) OR c.correction_status = 'ACTIVE')"
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

    # ── Transfers ────────────────────────────────────────────────────────

    def create_transfer_pair(
        self,
        security_id: str,
        trade_date: str,
        quantity: str,
        source_account_id: str,
        dest_account_id: str,
        cost_basis_override_eur: Optional[str] = None,
        transfer_fee: Optional[Dict[str, Any]] = None,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a paired TRANSFER_OUT + TRANSFER_IN.

        Raises:
            ValueError: same source/dest, missing fields
            InsufficientSharesError: source lacks shares at trade_date
        """
        self._require_portfolio()

        if source_account_id == dest_account_id:
            raise ValueError("source_account_id and dest_account_id must differ")

        qty = _d(quantity)
        if qty <= Decimal("0"):
            raise ValueError("quantity must be > 0")

        # Compute source available shares at trade_date
        available = self._compute_shares_at_date(
            account_id=source_account_id,
            security_id=security_id,
            as_of_date=trade_date,
        )
        if available < qty:
            raise InsufficientSharesError(
                available=str(available.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)),
                requested=str(qty.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)),
            )

        # Derive cost basis from source avg cost × quantity
        derived_cost_basis = self._compute_cost_basis_at_date(
            account_id=source_account_id,
            security_id=security_id,
            as_of_date=trade_date,
            quantity=qty,
        )
        derived_str = str(derived_cost_basis.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

        if cost_basis_override_eur is not None:
            effective_cost_str = str(_d(cost_basis_override_eur).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            overridden = True
        else:
            effective_cost_str = derived_str
            overridden = False

        now = self._now()
        group_id = f"trf_{uuid4().hex}"
        out_id = f"mvt_{uuid4().hex}"
        in_id = f"mvt_{uuid4().hex}"
        ticker = security_id.split(":")[-1] if ":" in security_id else security_id

        shared_fields: Dict[str, Any] = {
            "doc_type": "ledger_txn",
            "security_id": security_id,
            "ticker": ticker,
            "trade_date": trade_date,
            "quantity": str(qty.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)),
            "import_source": "manual",
            "correction_status": "ACTIVE",
            "transfer_group_id": group_id,
            "transfer_source_account_id": source_account_id,
            "transfer_dest_account_id": dest_account_id,
            "transfer_cost_basis_derived_eur": derived_str,
            "transfer_cost_basis_eur": effective_cost_str,
            "transfer_cost_basis_overridden": overridden,
            "created_at": now,
            "updated_at": now,
        }
        if transfer_fee:
            shared_fields["transfer_fee"] = transfer_fee
        if notes:
            shared_fields["notes"] = notes

        out_doc = {
            **shared_fields,
            "id": out_id,
            "account_id": source_account_id,
            "txn_type": "TRANSFER_OUT",
            "transfer_peer_id": in_id,
        }
        in_doc = {
            **shared_fields,
            "id": in_id,
            "account_id": dest_account_id,
            "txn_type": "TRANSFER_IN",
            "transfer_peer_id": out_id,
        }

        # Write both legs
        written_out = self.portfolio_container.upsert_item(out_doc)
        written_in = self.portfolio_container.upsert_item(in_doc)

        return {
            "transfer_out": _clean(written_out),
            "transfer_in": _clean(written_in),
            "transfer_group_id": group_id,
        }

    def _compute_shares_at_date(
        self, account_id: str, security_id: str, as_of_date: str
    ) -> Decimal:
        """Compute net shares held in an account for a security up to (and including) as_of_date."""
        movements = self._get_movements_up_to_date(account_id, security_id, as_of_date)
        shares = Decimal("0")
        for m in movements:
            txn_type = m.get("txn_type", "")
            qty = _d(m.get("quantity", "0"))
            if txn_type == "BUY":
                shares += qty
            elif txn_type == "SELL":
                sale_type = m.get("sales_type") or "ACCIONES"
                if sale_type == "ACCIONES":
                    shares -= qty
            elif txn_type == "TRANSFER_IN":
                shares += qty
            elif txn_type == "TRANSFER_OUT":
                shares -= qty
        return shares

    def _compute_cost_basis_at_date(
        self, account_id: str, security_id: str, as_of_date: str, quantity: Decimal
    ) -> Decimal:
        """Compute total carried cost basis for transferring `quantity` shares.

        Uses average cost × quantity from source account at as_of_date.
        """
        movements = self._get_movements_up_to_date(account_id, security_id, as_of_date)
        total_cost = Decimal("0")
        total_paid_shares = Decimal("0")
        for m in movements:
            txn_type = m.get("txn_type", "")
            if txn_type == "BUY":
                qty = _d(m.get("quantity", "0"))
                cost_status = m.get("cost_basis_status", "COMPLETE")
                if cost_status != "INCOMPLETE":
                    gross_eur = _d((m.get("gross") or {}).get("eur_amount", "0"))
                    fee_eur = _d((m.get("fees") or {}).get("total_eur", "0"))
                    total_cost += gross_eur + fee_eur
                    total_paid_shares += qty
            elif txn_type == "TRANSFER_IN":
                qty = _d(m.get("quantity", "0"))
                cost_basis = _d(m.get("transfer_cost_basis_eur", "0"))
                if qty > Decimal("0"):
                    total_cost += cost_basis
                    total_paid_shares += qty

        if total_paid_shares <= Decimal("0"):
            return Decimal("0")

        avg_cost = total_cost / total_paid_shares
        return (avg_cost * quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def _get_movements_up_to_date(
        self, account_id: str, security_id: str, as_of_date: str
    ) -> List[Dict[str, Any]]:
        """Fetch active movements for account+security up to as_of_date (inclusive)."""
        query = (
            "SELECT * FROM c "
            "WHERE c.doc_type = 'ledger_txn' "
            "AND NOT IS_DEFINED(c.deleted_at) "
            "AND (NOT IS_DEFINED(c.correction_status) OR c.correction_status = 'ACTIVE') "
            "AND c.security_id = @security_id "
            "AND c.trade_date <= @as_of_date "
            "ORDER BY c.trade_date ASC"
        )
        params = [
            {"name": "@security_id", "value": security_id},
            {"name": "@as_of_date", "value": as_of_date},
        ]
        try:
            items = list(self.portfolio_container.query_items(
                query=query,
                parameters=params,
                partition_key=account_id,
            ))
            return [_clean(d) for d in items if d.get("account_id") == account_id]
        except Exception as exc:
            logger.warning("_get_movements_up_to_date failed: %s", exc)
            return []

    # ── Reassignment ─────────────────────────────────────────────────────

    def reassign_movement(
        self,
        movement_id: str,
        source_account_id: str,
        dest_account_id: str,
        reason: str = "",
    ) -> Dict[str, Any]:
        """Move a single historical movement to a different account.

        Safe protocol:
          1. Read from source partition.
          2. Write new doc in dest partition with reassignment audit fields.
          3. Mark original SUPERSEDED in source partition.

        Returns {"original_id": ..., "new_id": ..., "dest_account_id": ...}.
        """
        self._require_portfolio()
        if source_account_id == dest_account_id:
            raise ValueError("same_account: source and dest must differ")

        doc = self.get_movement(movement_id, source_account_id)
        if doc is None:
            raise LookupError(f"Movement {movement_id!r} not found in account {source_account_id!r}")

        if doc.get("correction_status") in ("SUPERSEDED", "VOIDED"):
            raise ValueError(f"already_reassigned: movement is {doc.get('correction_status')}")

        now = self._now()
        new_id = f"mvt_{uuid4().hex}"

        new_doc = dict(doc)
        new_doc["id"] = new_id
        new_doc["account_id"] = dest_account_id
        new_doc["correction_status"] = "ACTIVE"
        new_doc["reassigned_from"] = {
            "account_id": source_account_id,
            "movement_id": movement_id,
        }
        new_doc["reassignment_reason"] = reason
        new_doc.pop("superseded_by", None)
        new_doc.pop("corrects_movement_id", None)
        new_doc["created_at"] = now
        new_doc["updated_at"] = now

        # Step 2: write to destination first (safe order)
        self.portfolio_container.upsert_item(new_doc)

        # Step 3: mark original SUPERSEDED
        try:
            orig_doc = self.portfolio_container.read_item(
                item=movement_id, partition_key=source_account_id
            )
            orig_doc["correction_status"] = "SUPERSEDED"
            orig_doc["superseded_by"] = new_id
            orig_doc["updated_at"] = now
            self.portfolio_container.replace_item(item=movement_id, body=orig_doc)
        except Exception as exc:
            logger.error(
                "reassign_movement: failed to mark original SUPERSEDED "
                "(new doc %s written; original %s in %s may need manual cleanup): %s",
                new_id, movement_id, source_account_id, exc
            )
            raise

        return {
            "original_id": movement_id,
            "new_id": new_id,
            "dest_account_id": dest_account_id,
        }

    def _fetch_reassign_candidates(
        self,
        source_account_id: str,
        security_id: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch active movements eligible for reassignment from source_account_id.

        This is the single canonical predicate shared by preview and execution.
        Read-only; no writes.
        """
        conditions = [
            "c.doc_type = 'ledger_txn'",
            "NOT IS_DEFINED(c.deleted_at)",
            "(NOT IS_DEFINED(c.correction_status) OR c.correction_status = 'ACTIVE')",
        ]
        params: List[Dict[str, Any]] = []

        if security_id:
            conditions.append("c.security_id = @security_id")
            params.append({"name": "@security_id", "value": security_id})
        if date_from:
            conditions.append("c.trade_date >= @date_from")
            params.append({"name": "@date_from", "value": date_from})
        if date_to:
            conditions.append("c.trade_date <= @date_to")
            params.append({"name": "@date_to", "value": date_to})

        where = " AND ".join(conditions)
        query = f"SELECT * FROM c WHERE {where} ORDER BY c.trade_date ASC"

        try:
            docs = list(self.portfolio_container.query_items(
                query=query,
                parameters=params,
                partition_key=source_account_id,
            ))
        except Exception as exc:
            logger.warning("_fetch_reassign_candidates query failed: %s", exc)
            docs = []

        # Partition key scoping: only return docs belonging to source_account_id
        return [_clean(d) for d in docs if d.get("account_id") == source_account_id]

    _PREVIEW_SAMPLE_LIMIT = 10  # bounded sample returned in preview responses

    def preview_batch_reassign(
        self,
        source_account_id: str,
        dest_account_id: str,
        security_id: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Preview a batch reassignment without performing any writes.

        Uses the exact same selection predicate as batch_reassign_movements.
        Callers must NOT pass a count back to the execution endpoint — the
        server re-derives the set at execution time.

        Returns:
            {
                "affected_count": N,
                "movement_ids": [...all matching IDs...],
                "sample": [...first 10 docs with key fields...],
                "source_account_id": "...",
                "dest_account_id": "...",
            }
        """
        self._require_portfolio()
        if source_account_id == dest_account_id:
            raise ValueError("same_account: source and dest must differ")

        candidates = self._fetch_reassign_candidates(
            source_account_id=source_account_id,
            security_id=security_id,
            date_from=date_from,
            date_to=date_to,
        )

        movement_ids = [d["id"] for d in candidates]
        sample = [
            {
                "id": d["id"],
                "security_id": d.get("security_id"),
                "txn_type": d.get("txn_type"),
                "trade_date": d.get("trade_date"),
                "quantity": d.get("quantity"),
                "account_id": d.get("account_id"),
            }
            for d in candidates[: self._PREVIEW_SAMPLE_LIMIT]
        ]

        return {
            "affected_count": len(candidates),
            "movement_ids": movement_ids,
            "sample": sample,
            "source_account_id": source_account_id,
            "dest_account_id": dest_account_id,
        }

    def batch_reassign_movements(
        self,
        source_account_id: str,
        dest_account_id: str,
        security_id: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        reason: str = "",
    ) -> Dict[str, Any]:
        """Reassign all matching active movements from source to dest account.

        All-or-nothing: on first failure all prior reassignments in this batch
        are rolled back via compensating operations. Never trusts a client-supplied
        count or ID list — always re-derives candidates at execution time.

        Returns {"reassigned_count": N, "skipped_count": 0, "ids": [new_id, ...]}.
        Raises ValueError("batch_reassign_failed: ...") on partial failure after rollback.
        """
        self._require_portfolio()
        if source_account_id == dest_account_id:
            raise ValueError("same_account: source and dest must differ")

        candidates = self._fetch_reassign_candidates(
            source_account_id=source_account_id,
            security_id=security_id,
            date_from=date_from,
            date_to=date_to,
        )

        completed: List[Dict[str, Any]] = []  # {"original_id", "new_id", "source_account_id"}

        for doc in candidates:
            try:
                result = self.reassign_movement(
                    movement_id=doc["id"],
                    source_account_id=source_account_id,
                    dest_account_id=dest_account_id,
                    reason=reason,
                )
                completed.append({
                    "original_id": doc["id"],
                    "new_id": result["new_id"],
                    "source_account_id": source_account_id,
                })
            except Exception as exc:
                logger.error(
                    "batch_reassign: failed on %s after %d successes, rolling back: %s",
                    doc.get("id"), len(completed), exc,
                )
                self._rollback_batch_reassign(completed, dest_account_id)
                raise ValueError(
                    f"batch_reassign_failed: operation rolled back after failure on "
                    f"movement {doc.get('id')!r}. {len(completed)} prior reassignment(s) "
                    f"were reversed. Cause: {exc}"
                )

        return {
            "reassigned_count": len(completed),
            "skipped_count": 0,
            "ids": [c["new_id"] for c in completed],
        }

    def _rollback_batch_reassign(
        self, completed: List[Dict[str, Any]], dest_account_id: str
    ) -> None:
        """Best-effort compensating rollback for a failed batch reassignment.

        For each already-reassigned entry:
          1. Delete the new document from the destination partition.
          2. Restore the original document to ACTIVE in the source partition.

        Logs errors with MANUAL CLEANUP REQUIRED for any step that fails.
        """
        for entry in completed:
            try:
                self.portfolio_container.delete_item(
                    item=entry["new_id"],
                    partition_key=dest_account_id,
                )
            except Exception as del_exc:
                logger.error(
                    "batch_rollback: could not delete new doc %s from %s: %s "
                    "(MANUAL CLEANUP REQUIRED)",
                    entry["new_id"], dest_account_id, del_exc,
                )

            try:
                original = self.portfolio_container.read_item(
                    item=entry["original_id"],
                    partition_key=entry["source_account_id"],
                )
                original["correction_status"] = "ACTIVE"
                original.pop("superseded_by", None)
                self.portfolio_container.upsert_item(original)
            except Exception as restore_exc:
                logger.error(
                    "batch_rollback: could not restore original %s in %s: %s "
                    "(MANUAL CLEANUP REQUIRED)",
                    entry["original_id"], entry["source_account_id"], restore_exc,
                )


