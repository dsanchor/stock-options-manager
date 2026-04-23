"""Shared parser for TradingView options chain data.

Extracts structured, agent-friendly option contract data from raw
TradingView scanner API responses stored in the cache layer.
"""

import datetime
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
  "calls": { "<YYYYMMDD>": { "<strike>": {contract}, ... } },
  "puts":  { "<YYYYMMDD>": { "<strike>": {contract}, ... } }
}
Calls and puts are grouped by expiration date (YYYYMMDD key). Each expiration
contains a dictionary of contracts keyed by strike price (e.g. "475.0", "472.5").
Contract fields:
  - opra_symbol: OPRA identifier (e.g. "OPRA:MSFT260427C475.0")
  - strike: Strike price in dollars
  - bid: Best bid price — what you RECEIVE when you SELL (open or close) this contract
  - ask: Best ask price — what you PAY when you BUY (open or close) this contract
  - mid: Theoretical mid-price (model-derived fair value, NOT necessarily (bid+ask)/2)
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

PREMIUM CALCULATION (CRITICAL — read carefully):
All strategies in this application SELL (write) options. When SELLING an option:
  - premium_per_contract = bid (you sell at the bid price — what the buyer pays you)
  - total_premium = bid × 100 (each contract = 100 shares)
  - premium_pct (covered call) = (bid / current_stock_price) × 100
  - premium_pct (cash-secured put) = (bid / strike) × 100
  - annualized_return = premium_pct × (365 / DTE)
Do NOT use 'ask' or 'mid' as the premium received. The 'bid' is always the
realistic premium a seller collects. Use 'mid' only for theoretical/fair-value
comparisons, never as actual premium income.

ROLL OPERATIONS (buying back + selling new):
  - buyback_cost = ask of your CURRENT contract (you BUY to close → pay the ask)
  - new_premium  = bid of the NEW target contract (you SELL to open → receive the bid)
  - net_credit   = new_premium - buyback_cost (positive = you collect, negative = you pay)

HOW TO LOOK UP A CONTRACT:
  Example: find the premium for selling an MSFT $475 call expiring 2026-04-27:
  1. calls["20260427"]["475.0"]["bid"] → that is the premium you receive when selling
  Example: find the buyback cost for your current MSFT $470 call expiring 2026-04-18:
  1. calls["20260418"]["470.0"]["ask"] → that is the cost to buy back (close) the position
  Direct key access — no searching required.

DATA INTEGRITY (MANDATORY):
  Every price you report (bid, ask, premium, buyback cost) MUST be the EXACT value
  from a contract in this JSON data. NEVER estimate, interpolate, round, or fabricate prices.
  State the full path and value: e.g., calls["20260427"]["475.0"]["ask"] = 3.00
  If the key path does not exist in the chain, state "contract not found in chain" — do NOT invent a price.
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

    calls: Dict[str, dict] = defaultdict(dict)
    puts: Dict[str, dict] = defaultdict(dict)
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

            strike_key = str(float(opt["strike"])) if opt["strike"] is not None else "0.0"
            if option_type == "call":
                calls[expiration][strike_key] = opt
            else:
                puts[expiration][strike_key] = opt

    # Sort strikes within each expiration, then sort expiration keys chronologically
    for bucket in (calls, puts):
        for exp in bucket:
            bucket[exp] = dict(sorted(bucket[exp].items(), key=lambda kv: float(kv[0])))

    sorted_calls = dict(sorted(calls.items()))
    sorted_puts = dict(sorted(puts.items()))

    return {
        "symbol": symbol,
        "timestamp": data_time,
        "calls": sorted_calls,
        "puts": sorted_puts,
    }


def filter_options_chain_for_position(
    chain: dict,
    current_strike: float,
    option_type: Optional[str] = None,
    num_strikes: int = 15,
) -> dict:
    """Filter a parsed options chain to ±num_strikes around current_strike.

    Keeps only strikes within range for each expiration in calls/puts.
    Adds a ``current_position`` key with the reference strike.
    """
    strike_val = float(current_strike)

    def _filter_bucket(bucket: dict) -> dict:
        filtered = {}
        for exp, strikes_dict in bucket.items():
            sorted_keys = sorted(strikes_dict.keys(), key=lambda k: float(k))
            # Find the index of the strike closest to current_strike
            closest_idx = min(
                range(len(sorted_keys)),
                key=lambda i: abs(float(sorted_keys[i]) - strike_val),
            ) if sorted_keys else 0
            lo = max(0, closest_idx - num_strikes)
            hi = min(len(sorted_keys), closest_idx + num_strikes + 1)
            kept = sorted_keys[lo:hi]
            if kept:
                filtered[exp] = {k: strikes_dict[k] for k in kept}
        return filtered

    result = {
        "symbol": chain.get("symbol", ""),
        "timestamp": chain.get("timestamp"),
        "current_position": {
            "strike": strike_val,
            "strike_key": str(float(strike_val)),
        },
    }
    if option_type:
        result["current_position"]["option_type"] = option_type

    result["calls"] = _filter_bucket(chain.get("calls", {}))
    result["puts"] = _filter_bucket(chain.get("puts", {}))
    return result


def filter_options_chain_by_delta(
    chain: dict,
    call_delta_range: tuple[float, float] = (0.15, 0.90),
    put_delta_range: tuple[float, float] = (-0.60, -0.15),
) -> dict:
    """Filter a parsed options chain to keep only contracts within delta ranges.

    Removes contracts with delta outside the specified ranges or with missing delta.
    This reduces noise for agents by eliminating deep ITM/OTM contracts.
    """
    def _filter_bucket(bucket: dict, delta_min: float, delta_max: float) -> dict:
        filtered = {}
        for exp, strikes_dict in bucket.items():
            kept = {}
            for strike_key, contract in strikes_dict.items():
                delta = contract.get("delta")
                if delta is not None and delta_min <= delta <= delta_max:
                    kept[strike_key] = contract
            if kept:
                filtered[exp] = kept
        return filtered

    return {
        "symbol": chain.get("symbol", ""),
        "timestamp": chain.get("timestamp"),
        "calls": _filter_bucket(chain.get("calls", {}), *call_delta_range),
        "puts": _filter_bucket(chain.get("puts", {}), *put_delta_range),
        **({"current_position": chain["current_position"]} if "current_position" in chain else {}),
    }


# ---------------------------------------------------------------------------
# Roll-direction filtering — narrows chain for Phase 2 based on roll type
# ---------------------------------------------------------------------------

# Roll types and their directional semantics (same for calls and puts)
_ROLL_STRIKE_FILTERS = {
    "ROLL_DOWN":         "below",
    "ROLL_UP":           "above",
    "ROLL_OUT":          "same",       # ±1 adjacent strike
    "ROLL_UP_AND_OUT":   "above_eq",
    "ROLL_DOWN_AND_OUT": "below_eq",
}

# Rolls containing "OUT" require strictly later expirations
_STRICT_LATER_ROLLS = {"ROLL_OUT", "ROLL_UP_AND_OUT", "ROLL_DOWN_AND_OUT"}


def filter_options_chain_by_roll_direction(
    chain: dict,
    current_strike: float,
    current_expiration: str,
    roll_type: str,
    option_type: str,
) -> dict:
    """Filter an already-filtered chain based on the roll direction from Phase 1.

    Narrows strikes and expirations so Phase 2 only sees candidates that are
    valid for the given roll type.  Unrecognised roll types pass the chain
    through unchanged (safe fallback).

    Parameters
    ----------
    chain : dict
        Structured chain dict (output of ``filter_options_chain_by_delta``).
    current_strike : float
        The strike of the current position being rolled.
    current_expiration : str
        Expiration of the current position (``YYYY-MM-DD`` or ``YYYYMMDD``).
    roll_type : str
        Roll action from Phase 1 (e.g. ``ROLL_DOWN``, ``ROLL_UP_AND_OUT``).
    option_type : str
        ``"call"`` or ``"put"``.
    """
    direction = _ROLL_STRIKE_FILTERS.get(roll_type)
    if direction is None:
        logger.warning(
            "filter_options_chain_by_roll_direction: unknown roll_type '%s' — returning chain unchanged",
            roll_type,
        )
        return chain

    # Normalise expiration to YYYYMMDD for chain-key comparison
    exp_key = current_expiration.replace("-", "")
    strict_later = roll_type in _STRICT_LATER_ROLLS

    # Determine which bucket to filter based on option_type
    bucket_key = "calls" if option_type == "call" else "puts"
    bucket = chain.get(bucket_key, {})

    # Pre-compute adjacent strikes for ROLL_OUT (±1 nearest)
    all_strikes: set[float] = set()
    for strikes_dict in bucket.values():
        all_strikes.update(float(k) for k in strikes_dict)
    sorted_strikes = sorted(all_strikes)

    adjacent_strikes: set[float] = set()
    if direction == "same" and sorted_strikes:
        # Find index of the closest strike to current_strike
        closest_idx = min(
            range(len(sorted_strikes)),
            key=lambda i: abs(sorted_strikes[i] - current_strike),
        )
        adjacent_strikes.add(sorted_strikes[closest_idx])
        if closest_idx > 0:
            adjacent_strikes.add(sorted_strikes[closest_idx - 1])
        if closest_idx < len(sorted_strikes) - 1:
            adjacent_strikes.add(sorted_strikes[closest_idx + 1])

    def _strike_ok(strike_val: float) -> bool:
        if direction == "below":
            return strike_val < current_strike
        elif direction == "above":
            return strike_val > current_strike
        elif direction == "below_eq":
            return strike_val <= current_strike
        elif direction == "above_eq":
            return strike_val >= current_strike
        elif direction == "same":
            return strike_val in adjacent_strikes
        return True  # fallback: keep

    def _exp_ok(exp: str) -> bool:
        if strict_later:
            return exp > exp_key
        return exp >= exp_key

    filtered_bucket: dict = {}
    for exp, strikes_dict in bucket.items():
        if not _exp_ok(exp):
            continue
        kept = {
            k: v for k, v in strikes_dict.items()
            if _strike_ok(float(k))
        }
        if kept:
            filtered_bucket[exp] = kept

    # Preserve the other bucket untouched and keep chain metadata
    other_key = "puts" if bucket_key == "calls" else "calls"
    result = {
        "symbol": chain.get("symbol", ""),
        "timestamp": chain.get("timestamp"),
        bucket_key: filtered_bucket,
        other_key: chain.get(other_key, {}),
    }
    if "current_position" in chain:
        result["current_position"] = chain["current_position"]
    return result


# ---------------------------------------------------------------------------
# Pre-computed roll candidate table for Phase 2
# ---------------------------------------------------------------------------

def _fmt_exp(exp: str) -> str:
    """Convert YYYYMMDD → YYYY-MM-DD for display."""
    if len(exp) == 8 and exp.isdigit():
        return f"{exp[:4]}-{exp[4:6]}-{exp[6:]}"
    return exp


def format_roll_candidates_table(
    chain: dict,
    current_strike: float,
    current_expiration: str,
    option_type: str,
    underlying_price: float,
    roll_type: str,
    buyback_cost: float | None = None,
) -> str:
    """Build a flat markdown table of roll candidates with pre-computed economics.

    Parameters
    ----------
    chain : dict
        Direction-filtered chain (output of ``filter_options_chain_by_roll_direction``).
    current_strike : float
        Strike of the position being rolled.
    current_expiration : str
        Expiration of the current position (YYYY-MM-DD or YYYYMMDD).
    option_type : str
        ``"call"`` or ``"put"``.
    underlying_price : float
        Current price of the underlying stock.
    roll_type : str
        Roll action from Phase 1 (e.g. ``ROLL_DOWN``).
    buyback_cost : float | None
        Ask price of the current contract (cost to buy-to-close).  Pass this
        explicitly because the direction-filtered chain usually excludes the
        current contract.  When *None*, the function attempts a fallback
        lookup in ``chain`` but this will often miss.

    Returns
    -------
    str
        Human-readable text block with current position summary and candidate table.
    """
    bucket_key = "calls" if option_type == "call" else "puts"
    bucket = chain.get(bucket_key, {})
    symbol = chain.get("symbol", "")
    today = datetime.date.today()

    # Normalise current expiration to YYYYMMDD for chain lookup
    exp_key = current_expiration.replace("-", "")
    strike_key = str(float(current_strike))

    # --- Find current position contract (may be absent after direction filter) ---
    current_contract = None
    if exp_key in bucket and strike_key in bucket[exp_key]:
        current_contract = bucket[exp_key][strike_key]

    # Use explicitly-provided buyback_cost; fall back to chain lookup only if needed
    if buyback_cost is None and current_contract and current_contract.get("ask") is not None:
        buyback_cost = float(current_contract["ask"])

    # --- Current position summary ---
    current_exp_display = _fmt_exp(exp_key)
    try:
        current_exp_date = datetime.date(int(exp_key[:4]), int(exp_key[4:6]), int(exp_key[6:]))
        current_dte = (current_exp_date - today).days
    except (ValueError, IndexError):
        current_dte = None

    lines = [f"CURRENT POSITION:"]
    lines.append(f"  Symbol: {symbol} | Type: {option_type}")
    dte_str = f" ({current_dte} DTE)" if current_dte is not None else ""
    lines.append(f"  Strike: ${current_strike:.1f} | Expiration: {current_exp_display}{dte_str}")

    if current_contract:
        bid_str = f"${current_contract.get('bid', 'N/A')}" if current_contract.get('bid') is not None else "N/A"
        ask_str = f"${current_contract.get('ask', 'N/A')}" if current_contract.get('ask') is not None else "N/A"
        delta_str = f"{current_contract.get('delta', 'N/A')}" if current_contract.get('delta') is not None else "N/A"
        theta_str = f"{current_contract.get('theta', 'N/A')}" if current_contract.get('theta') is not None else "N/A"
        lines.append(f"  Bid: {bid_str} | Ask: {ask_str} | Delta: {delta_str} | Theta: {theta_str}")
    if buyback_cost is not None:
        lines.append(f"  Buyback cost (ask): ${buyback_cost:.2f} per share (${buyback_cost * 100:.2f} per contract)")
    else:
        lines.append("  Buyback cost: NOT AVAILABLE — current contract not in chain data. Use the buyback cost from Phase 1 handoff if available.")

    # --- Build candidate rows ---
    candidates = []
    for exp, strikes_dict in sorted(bucket.items()):
        for sk, contract in sorted(strikes_dict.items(), key=lambda kv: float(kv[0])):
            # Skip the current position itself
            if exp == exp_key and sk == strike_key:
                continue
            bid = contract.get("bid")
            if bid is None or bid == 0:
                continue

            bid = float(bid)
            ask_val = float(contract["ask"]) if contract.get("ask") is not None else None
            delta = contract.get("delta")
            theta = contract.get("theta")
            strike_val = float(sk)
            exp_display = _fmt_exp(exp)

            try:
                exp_date = datetime.date(int(exp[:4]), int(exp[4:6]), int(exp[6:]))
                dte = (exp_date - today).days
            except (ValueError, IndexError):
                dte = 0

            net_credit = (bid - buyback_cost) if buyback_cost is not None else None

            if option_type == "call" and underlying_price > 0:
                premium_pct = (bid / underlying_price) * 100
            elif option_type == "put" and strike_val > 0:
                premium_pct = (bid / strike_val) * 100
            else:
                premium_pct = 0.0

            ann_ret = (premium_pct * 365 / dte) if dte > 0 else 0.0

            candidates.append({
                "strike": strike_val,
                "exp": exp_display,
                "dte": dte,
                "delta": delta,
                "theta": theta,
                "bid": bid,
                "ask": ask_val,
                "net_credit": net_credit,
                "premium_pct": premium_pct,
                "ann_ret": ann_ret,
            })

    # Sort by net_credit descending (best credit first); fall back to bid desc if no buyback
    if buyback_cost is not None:
        candidates.sort(key=lambda c: c["net_credit"] if c["net_credit"] is not None else -9999, reverse=True)
    else:
        candidates.sort(key=lambda c: c["bid"], reverse=True)

    if not candidates:
        lines.append("")
        lines.append(f"NO VALID CANDIDATES found for {roll_type}. Consider CLOSE.")
        return "\n".join(lines)

    # --- Format table ---
    lines.append("")
    lines.append(f"ROLL CANDIDATES ({roll_type} — {len(candidates)} candidates, sorted by net credit):")
    header = "| #  | Strike | Expiration | DTE | Delta |  Bid  |  Ask  | New Prem | Buyback | Net Credit | Prem% | Ann.Ret% |"
    sep    = "|----|--------|------------|-----|-------|-------|-------|----------|---------|------------|-------|----------|"
    lines.append(header)
    lines.append(sep)

    for i, c in enumerate(candidates, 1):
        delta_s = f"{c['delta']:.2f}" if c["delta"] is not None else "  -  "
        ask_s = f"{c['ask']:.2f}" if c["ask"] is not None else "  -  "
        buyback_s = f"{buyback_cost:.2f}" if buyback_cost is not None else "  N/A "
        if c["net_credit"] is not None:
            nc_s = f"{c['net_credit']:+.2f}"
        else:
            nc_s = "  N/A "
        row = (
            f"| {i:>2} "
            f"| {c['strike']:<6.1f} "
            f"| {c['exp']:>10} "
            f"| {c['dte']:>3} "
            f"| {delta_s:>5} "
            f"| {c['bid']:>5.2f} "
            f"| {ask_s:>5} "
            f"| {c['bid']:>8.2f} "
            f"| {buyback_s:>7} "
            f"| {nc_s:>10} "
            f"| {c['premium_pct']:>4.1f}% "
            f"| {c['ann_ret']:>7.1f}% |"
        )
        lines.append(row)

    # --- Notes ---
    lines.append("")
    lines.append("NOTES:")
    if buyback_cost is not None:
        lines.append(f"- Buyback cost is FIXED at ${buyback_cost:.2f} (current contract ask) for all candidates")
    lines.append("- Net Credit = New Premium (bid) - Buyback Cost (ask). Positive = you collect, negative = you pay")
    lines.append("- All prices are per share. Multiply by 100 for per-contract amounts")
    if option_type == "call":
        lines.append("- Premium% for calls = bid / underlying_price × 100")
    else:
        lines.append("- Premium% for puts = bid / strike × 100")
    lines.append("- Ann.Ret% = Premium% × 365 / DTE")

    return "\n".join(lines)
