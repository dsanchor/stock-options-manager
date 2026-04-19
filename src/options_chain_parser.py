"""Shared parser for TradingView options chain data.

Extracts structured, agent-friendly option contract data from raw
TradingView scanner API responses stored in the cache layer.
"""

import json
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional


def parse_options_chain(raw: str, symbol: str = "") -> dict:
    """Parse raw TradingView options chain data into agent-friendly structured format.

    Returns dict with keys: symbol, timestamp, calls, puts
    - calls/puts are dicts keyed by expiration date (YYYYMMDD string)
    - Each expiration contains a list of option contracts with key-value fields
    - Returns empty calls/puts dicts if parsing fails
    """
    if not raw:
        return {"symbol": symbol, "timestamp": None, "calls": {}, "puts": {}}

    # Strip header prefix if present
    raw = re.sub(r"^OPTIONS CHAIN DATA\s*\([^)]*\)\s*:\s*\n*", "", raw).strip()

    # Parse JSON — try whole string first, fall back to splitting on blank lines
    all_items: list = []
    data_time: Optional[Any] = None

    def _extract(parsed: dict):
        nonlocal data_time
        items = parsed.get("symbols", parsed.get("data", []))
        all_items.extend(items)
        if "time" in parsed and data_time is None:
            data_time = parsed["time"]

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            _extract(parsed)
    except (json.JSONDecodeError, TypeError):
        # Multiple JSON objects concatenated with blank lines
        for block in re.split(r"\n{2,}", raw):
            block = block.strip()
            if not block:
                continue
            try:
                parsed = json.loads(block)
                if isinstance(parsed, dict):
                    _extract(parsed)
            except (json.JSONDecodeError, TypeError):
                continue

    if not all_items:
        return {"symbol": symbol, "timestamp": data_time, "calls": {}, "puts": {}}

    calls: Dict[str, list] = defaultdict(list)
    puts: Dict[str, list] = defaultdict(list)

    for item in all_items:
        f = item.get("f")
        if not f or len(f) < 15:
            continue
        option_type = f[7]  # "call" or "put"
        expiration = str(f[4]) if f[4] is not None else None
        if not expiration:
            continue

        # Field mapping (from TradingView scanner API):
        # 0=ask, 1=bid, 2=currency, 3=delta, 4=expiration, 5=gamma,
        # 6=iv, 7=option-type, 8=pricescale, 9=rho, 10=root,
        # 11=strike, 12=theoPrice(mid), 13=theta, 14=vega, 15=bid_iv, 16=ask_iv
        opt = {
            "opra_symbol": item.get("s", ""),
            "strike": f[11],
            "bid": f[1],
            "ask": f[0],
            "mid": f[12],
            "iv": f[6],
            "delta": f[3],
            "gamma": f[5],
            "theta": f[13],
            "vega": f[14],
            "rho": f[9],
            "currency": f[2],
            "expiration": expiration,
            "option_type": option_type,
        }

        # bid_iv and ask_iv only present when response includes 17+ fields
        if len(f) >= 17:
            opt["bid_iv"] = f[15]
            opt["ask_iv"] = f[16]

        if option_type == "call":
            calls[expiration].append(opt)
        elif option_type == "put":
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
