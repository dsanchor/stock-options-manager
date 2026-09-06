"""Idempotent enrollment of symbol_config documents.

`ensure_symbol_config` guarantees a symbol_config document exists in the
symbols container for the given MIC:TICKER security_id.  Existing configs are
*never* overwritten.  Missing configs are created with every agent, alert,
notification, and scheduling flag set to False/disabled.  Create races (HTTP
409) are resolved by re-reading and returning the winner.

Cross-container safety: callers must invoke this only *after* the authoritative
ledger write has succeeded.  A failure here must never roll back the ledger.
Log the exception; let the caller decide whether to surface a warning.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict

from azure.cosmos.exceptions import CosmosResourceNotFoundError

logger = logging.getLogger(__name__)

_COSMOS_SYSTEM_KEYS = frozenset({"_rid", "_self", "_etag", "_attachments", "_ts"})


def _clean(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in doc.items() if k not in _COSMOS_SYSTEM_KEYS}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_symbol_config(
    symbols_container,
    security_id: str,
    source: str,
) -> Dict[str, Any]:
    """Idempotently ensure a symbol_config exists for the ticker in security_id.

    Args:
        symbols_container: Cosmos container client for the 'symbols' container.
        security_id: Canonical MIC:TICKER identifier, e.g. "XNYS:AAPL".
        source: Caller label for audit — one of "import_commit",
                "manual_movement", "transfer_in", "add_symbol", "backfill".

    Returns:
        The existing or newly created symbol_config document (Cosmos keys stripped).

    Raises:
        ValueError: If security_id is not MIC:TICKER format, or if the
                    corresponding security_master document does not exist
                    in the symbols container.
        RuntimeError: If a 409 create-race occurs and the re-read also fails.
        Other Cosmos exceptions (429, 503, …) propagate to caller.
    """
    if ":" not in security_id:
        raise ValueError(
            f"security_id must be MIC:TICKER format, got {security_id!r}"
        )
    mic, ticker = security_id.split(":", 1)
    mic = mic.upper()
    ticker = ticker.upper()
    config_id = f"config_{ticker}"

    # ── Step 1: point-read existing config (the happy path for idempotency) ──
    try:
        doc = symbols_container.read_item(item=config_id, partition_key=ticker)
        existing_sid = doc.get("security_id")
        if existing_sid and existing_sid != security_id:
            logger.warning(
                "ensure_symbol_config: config_%s already has security_id=%r "
                "(caller wants %r) — no-op; collision visible here for review",
                ticker,
                existing_sid,
                security_id,
            )
        return _clean(doc)
    except CosmosResourceNotFoundError:
        pass  # config missing — proceed to create

    # ── Step 2: look up security_master to obtain company_name / exchange ──
    sec_doc_id = f"sec_{mic}_{ticker}"
    try:
        sec_doc = symbols_container.read_item(item=sec_doc_id, partition_key=ticker)
        company_name: str = sec_doc.get("company_name", ticker)
        exchange_mic: str = sec_doc.get("exchange_mic", mic)
    except CosmosResourceNotFoundError:
        raise ValueError(
            f"security_master not found for security_id={security_id!r} "
            f"(looked for id={sec_doc_id!r} in partition {ticker!r}); "
            "caller must create the security_master before calling ensure_symbol_config"
        )

    # ── Step 3: create new config with everything disabled ──
    now = _now()
    config_doc: Dict[str, Any] = {
        "id": config_id,
        "symbol": ticker,
        "doc_type": "symbol_config",
        "security_id": security_id,
        "exchange": exchange_mic,
        "display_name": company_name,
        "total_shares": 0,
        "watchlist": {
            "covered_call": False,
            "cash_secured_put": False,
            "buy_tracker": False,
        },
        "telegram_notifications_enabled": False,
        "positions": [],
        "created_at": now,
        "updated_at": now,
        "_auto_enrolled": True,
        "_auto_enrolled_source": source,
        "_auto_enrolled_at": now,
    }

    try:
        created = symbols_container.create_item(config_doc)
        logger.info(
            "ensure_symbol_config: created config_%s (source=%s, security_id=%s)",
            ticker,
            source,
            security_id,
        )
        return _clean(created)
    except Exception as exc:
        exc_str = str(exc)
        # HTTP 409 Conflict → another writer won the race; re-read and return winner
        if "409" in exc_str or "Conflict" in exc_str:
            logger.info(
                "ensure_symbol_config: 409 race on config_%s — re-reading winner",
                ticker,
            )
            try:
                doc = symbols_container.read_item(
                    item=config_id, partition_key=ticker
                )
                return _clean(doc)
            except CosmosResourceNotFoundError:
                raise RuntimeError(
                    f"409 race on config_{ticker} but re-read returned not-found; "
                    "transient state — retry"
                )
        raise
