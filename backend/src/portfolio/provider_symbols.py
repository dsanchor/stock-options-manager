"""Provider-specific symbol support for security_master documents.

Implements the suffix table, suggestion helper, and validation function
defined in the danny-provider-symbol-import-contract.md (§2, §5).

Phase 1: yfinance only.  Keys for future providers (bloomberg, refinitiv …)
require only a one-line addition to MIC_TO_YFINANCE_SUFFIX.
"""

from __future__ import annotations

import re
from typing import Dict, Optional

# ---------------------------------------------------------------------------
# §2 — MIC → yfinance suffix table
# ---------------------------------------------------------------------------

MIC_TO_YFINANCE_SUFFIX: Dict[str, str] = {
    "XMAD": ".MC",   # BME (Madrid)
    "XAMS": ".AS",   # Euronext Amsterdam
    "XLON": ".L",    # London Stock Exchange
    "XPAR": ".PA",   # Euronext Paris
    "XETR": ".DE",   # Deutsche Börse (Xetra)
    "XSWX": ".SW",   # SIX Swiss Exchange
    "XBRU": ".BR",   # Euronext Brussels
    "XLIS": ".LS",   # Euronext Lisbon
    "XNYS": "",      # NYSE — no suffix
    "XNAS": "",      # NASDAQ — no suffix
}


def suggest_yfinance_symbol(ticker: str, exchange_mic: str) -> Optional[str]:
    """Return the suggested yfinance symbol, or None for unknown MIC.

    Examples:
        suggest_yfinance_symbol("ENG", "XMAD")  → "ENG.MC"
        suggest_yfinance_symbol("AAPL", "XNYS") → "AAPL"
        suggest_yfinance_symbol("FOO", "XZZZ")  → None
    """
    suffix = MIC_TO_YFINANCE_SUFFIX.get(exchange_mic.upper())
    if suffix is None:
        return None
    return f"{ticker.upper()}{suffix}"


# ---------------------------------------------------------------------------
# Yahoo symbol resolution contract (danny-yahoo-symbol-resolution-contract.md)
# ---------------------------------------------------------------------------

# Legacy free-text exchange labels pre-dating the security_master/MIC rollout.
# The pre-unification `/api/symbols` add flow (DGI-screener-sourced, always
# US) stores these display names — never a MIC code — in `exchange`. They
# are unambiguous (always a US bare ticker, exactly like XNYS/XNAS), so they
# are treated as bare-ticker equivalents here rather than failing closed.
# This is NOT a second suffix table: the resolved suffix is always empty.
_LEGACY_US_EXCHANGE_ALIASES = frozenset({"NYSE", "NASDAQ", "AMEX"})


def resolve_yfinance_symbol(
    ticker: str,
    exchange_mic: Optional[str],
    security_master_doc: Optional[dict] = None,
) -> Optional[str]:
    """Resolve the Yahoo Finance symbol to fetch for a portfolio ticker.

    Single resolution point for the enrichment path (and any other caller
    that needs to talk to Yahoo for a non-US-screener symbol). Reuses
    ``MIC_TO_YFINANCE_SUFFIX``/``suggest_yfinance_symbol`` — no second
    suffix table.

    Precedence (highest wins):
      1. ``security_master_doc["provider_symbols"]["yfinance"]`` — explicit
         per-security override (e.g. Nestlé "NESN" → "NESN.SW").
      2. ``suggest_yfinance_symbol(ticker, exchange_mic)`` — canonical
         MIC-to-suffix mapping (XNYS/XNAS unaffected — empty suffix).
      3. Unknown or missing MIC → ``None`` (fail closed; callers must never
         fall back to the bare ticker for a non-US MIC).

    Args:
        ticker: Local/canonical ticker symbol (e.g. "NESN").
        exchange_mic: The security's exchange MIC (e.g. "XSWX"), or falsy
            if unknown/unavailable.
        security_master_doc: Optional security_master projection/document
            that may carry a ``provider_symbols`` override map.

    Returns:
        The resolved Yahoo Finance symbol, or None if it cannot be resolved
        safely (unknown MIC, missing MIC, and no override present).
    """
    if security_master_doc:
        override = (security_master_doc.get("provider_symbols") or {}).get("yfinance")
        if override:
            return override

    if not exchange_mic:
        return None

    if exchange_mic.strip().upper() in _LEGACY_US_EXCHANGE_ALIASES:
        return ticker.upper()

    return suggest_yfinance_symbol(ticker, exchange_mic)


# ---------------------------------------------------------------------------
# §5 — Validation
# ---------------------------------------------------------------------------

_PROVIDER_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,29}$")
_PROVIDER_VALUE_RE = re.compile(r"^[A-Za-z0-9._^\-]{1,30}$")


def validate_provider_symbols(ps: object) -> Dict[str, str]:
    """Validate and normalise a provider_symbols map.

    - Strips leading/trailing whitespace from values.
    - Drops empty values (key removed from map).
    - Returns cleaned map (may be empty dict).
    - Raises ValueError on invalid key format, invalid value, or >10 entries.
    """
    if not ps or not isinstance(ps, dict):
        return {}
    if len(ps) > 10:
        raise ValueError("provider_symbols: max 10 entries")
    cleaned: Dict[str, str] = {}
    for k, v in ps.items():
        if not isinstance(k, str) or not _PROVIDER_KEY_RE.match(k):
            raise ValueError(f"provider_symbols: invalid key '{k}'")
        v = str(v).strip()
        if not v:
            continue  # empty after trim → omit
        if not _PROVIDER_VALUE_RE.match(v):
            raise ValueError(f"provider_symbols[{k!r}]: invalid value '{v}'")
        cleaned[k] = v
    return cleaned
