"""US-options exchange eligibility — single source of truth.

Amendment J: copilot-directive-20260906-us-only-symbol-actions.md
Contract: danny-unified-watchlist-contract.md §J.1

Both ``is_us_options_eligible`` and ``enforce_us_options_eligible`` live here
to prevent drift between UI-hiding (frontend) and backend enforcement.
"""
from __future__ import annotations

from fastapi.responses import JSONResponse

US_OPTIONS_ELIGIBLE_MICS: frozenset[str] = frozenset({"XNYS", "XNAS"})


def is_us_options_eligible(exchange_mic: str | None) -> bool:
    """Return True if the exchange MIC supports US-listed options analysis.

    Used by BOTH the symbol detail API (to expose the flag) and all action
    endpoints (to enforce eligibility). Keeping the predicate in one file
    prevents drift between UI-hiding and backend enforcement.

    Fails closed: None / empty string → False.
    """
    if not exchange_mic:
        return False
    return exchange_mic.strip().upper() in US_OPTIONS_ELIGIBLE_MICS


def enforce_us_options_eligible(
    doc: dict | None,
    security_field: dict | None = None,
) -> JSONResponse | None:
    """Return a 403 JSONResponse if the symbol is not US-options-eligible.

    Returns None if eligible (caller proceeds normally).

    MIC resolution order (§J.1.3):
    1. security_field.exchange_mic  (canonical, from security_master)
    2. doc.exchange                 (set by ensure_symbol_config)
    3. doc.security_id MIC prefix   (e.g. "XMAD:REP" → "XMAD")
    4. Not eligible (fail-closed)

    Args:
        doc: symbol_config document.
        security_field: optional security_master projection (has 'exchange_mic').
    """
    effective_mic = ""
    if security_field and security_field.get("exchange_mic"):
        effective_mic = security_field["exchange_mic"]
    elif doc and doc.get("exchange"):
        effective_mic = doc["exchange"]
    elif doc and doc.get("security_id") and ":" in doc["security_id"]:
        effective_mic = doc["security_id"].split(":")[0]

    if not is_us_options_eligible(effective_mic):
        return JSONResponse(
            {
                "error": "options_not_eligible",
                "detail": (
                    "Options features are available only for US-listed securities "
                    f"(NYSE/NASDAQ). This symbol's exchange "
                    f"({effective_mic or 'unknown'}) is not eligible."
                ),
            },
            status_code=403,
        )
    return None  # eligible — proceed
