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
