"""Shared parser for TradingView options chain data.

Extracts structured, agent-friendly option contract data from raw
TradingView scanner API responses stored in the cache layer.
"""

import json
import logging
import re
from collections import defaultdict
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Reusable schema description — import and prepend wherever options chain
# JSON is injected into agent prompts, chat contexts, or reports.
# ---------------------------------------------------------------------------
OPTIONS_CHAIN_SCHEMA_DESCRIPTION = """\
OPTIONS CHAIN FORMAT:
The options chain is a JSON object with the following structure:
{
  "symbol": "<TICKER>",
  "timestamp": "<ISO 8601 fetch time>",
  "calls": { "<YYYYMMDD>": [ ...contracts... ] },
  "puts":  { "<YYYYMMDD>": [ ...contracts... ] }
}
Calls and puts are grouped by expiration date (YYYYMMDD key). Each expiration
contains a list of contracts sorted by strike price. Contract fields:
  - opra_symbol: OPRA identifier (e.g. "OPRA:MSFT260427C475.0")
  - strike: Strike price in dollars
  - bid / ask: Best bid and ask prices
  - mid: Theoretical mid-price
  - iv: Implied volatility (decimal, e.g. 0.364 = 36.4%)
  - delta: Delta (0 to 1 for calls, -1 to 0 for puts)
  - gamma: Gamma (rate of delta change)
  - theta: Theta (daily time decay, negative value)
  - vega: Vega (sensitivity to volatility)
  - rho: Rho (sensitivity to interest rates)
  - currency: Currency code (usually "USD")
  - expiration: Expiration date as YYYYMMDD string
  - option_type: "call" or "put"
  - bid_iv / ask_iv: Bid/ask implied volatilities (optional)
"""

# Canonical field names we expose on each contract
_FIELD_MAP = {
    "ask": "ask",
    "bid": "bid",
    "currency": "currency",
    "delta": "delta",
    "expiration": "expiration",
    "gamma": "gamma",
    "iv": "iv",
    "option-type": "option_type",
    "pricescale": "pricescale",
    "rho": "rho",
    "root": "root",
    "strike": "strike",
    "theoprice": "mid",  # theoPrice → mid
    "theta": "theta",
    "vega": "vega",
    "bid_iv": "bid_iv",
    "ask_iv": "ask_iv",
}


def parse_options_chain(raw: str, symbol: str = "") -> dict:
    """Parse raw TradingView options chain data into agent-friendly structured format.

    Returns dict with keys: symbol, timestamp, calls, puts
    - calls/puts are dicts keyed by expiration date (YYYYMMDD string)
    - Each expiration contains a list of option contracts with key-value fields
    - Returns empty calls/puts dicts if parsing fails

    Uses the ``fields`` array from each JSON response to build a dynamic
    index→name mapping so the parser is resilient to field-order changes.
    """
    if not raw:
        return {"symbol": symbol, "timestamp": None, "calls": {}, "puts": {}}

    # Strip header prefix if present
    raw = re.sub(r"^OPTIONS CHAIN DATA\s*\([^)]*\)\s*:\s*\n*", "", raw).strip()

    # Parse JSON — try whole string first, fall back to splitting on blank lines
    parsed_blocks: list[dict] = []

    def _try_parse(text: str):
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                parsed_blocks.append(obj)
        except (json.JSONDecodeError, TypeError):
            pass

    _try_parse(raw)
    if not parsed_blocks:
        for block in re.split(r"\n{2,}", raw):
            block = block.strip()
            if block:
                _try_parse(block)

    if not parsed_blocks:
        logger.warning("options_chain_parser: no valid JSON found (raw length=%d)", len(raw))
        return {"symbol": symbol, "timestamp": None, "calls": {}, "puts": {}}

    calls: Dict[str, list] = defaultdict(list)
    puts: Dict[str, list] = defaultdict(list)
    data_time: Optional[Any] = None

    for parsed in parsed_blocks:
        items = parsed.get("symbols", parsed.get("data", []))
        if "time" in parsed and data_time is None:
            data_time = parsed["time"]

        # Build dynamic field index map from the response's "fields" array
        fields_arr = parsed.get("fields", [])
        if fields_arr:
            idx_map = {}
            for i, name in enumerate(fields_arr):
                canon = _FIELD_MAP.get(name.lower(), name.lower().replace("-", "_"))
                idx_map[canon] = i
        else:
            # Fallback to hardcoded positions (legacy)
            idx_map = {
                "ask": 0, "bid": 1, "currency": 2, "delta": 3,
                "expiration": 4, "gamma": 5, "iv": 6, "option_type": 7,
                "pricescale": 8, "rho": 9, "root": 10, "strike": 11,
                "mid": 12, "theta": 13, "vega": 14, "bid_iv": 15, "ask_iv": 16,
            }

        opt_type_idx = idx_map.get("option_type")
        exp_idx = idx_map.get("expiration")
        if opt_type_idx is None or exp_idx is None:
            logger.warning("options_chain_parser: missing option_type/expiration in fields")
            continue

        for item in items:
            f = item.get("f")
            if not f or len(f) <= max(opt_type_idx, exp_idx):
                continue

            option_type = f[opt_type_idx]
            expiration = str(f[exp_idx]) if f[exp_idx] is not None else None
            if not expiration or option_type not in ("call", "put"):
                continue

            def _get(key: str):
                i = idx_map.get(key)
                return f[i] if i is not None and i < len(f) else None

            opt = {
                "opra_symbol": item.get("s", ""),
                "strike": _get("strike"),
                "bid": _get("bid"),
                "ask": _get("ask"),
                "mid": _get("mid"),
                "iv": _get("iv"),
                "delta": _get("delta"),
                "gamma": _get("gamma"),
                "theta": _get("theta"),
                "vega": _get("vega"),
                "rho": _get("rho"),
                "currency": _get("currency"),
                "expiration": expiration,
                "option_type": option_type,
            }

            bid_iv = _get("bid_iv")
            ask_iv = _get("ask_iv")
            if bid_iv is not None:
                opt["bid_iv"] = bid_iv
            if ask_iv is not None:
                opt["ask_iv"] = ask_iv

            if option_type == "call":
                calls[expiration].append(opt)
            else:
                puts[expiration].append(opt)

    # Sort within each expiration by strike, then sort keys chronologically
    for bucket in (calls, puts):
        for exp in bucket:
            bucket[exp].sort(key=lambda o: (o["strike"] or 0))

    sorted_calls = dict(sorted(calls.items()))
    sorted_puts = dict(sorted(puts.items()))

    return {
        "symbol": symbol,
        "timestamp": data_time,
        "calls": sorted_calls,
        "puts": sorted_puts,
    }
