"""TradingView data fetcher — hybrid BeautifulSoup + Playwright.

Overview, technicals, forecast, and dividends are fetched via requests +
BeautifulSoup (with TradingView scanner API fallback).  The options chain
still uses Playwright because it requires browser-level API interception.

Returns structured JSON strings for the four BS4-based fetchers and raw
text for the options chain.  All return types are ``str`` so callers
(``fetch_all``, ``agent_runner``, ``web/app.py``) work unchanged.
"""

import asyncio
import json
import logging
import random
import re
import time
from datetime import datetime, timezone

import requests as _requests
from requests.exceptions import HTTPError
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from src import tv_cache

logger = logging.getLogger(__name__)

# ======================================================================
# Anti-bot detection: User-Agent rotation and realistic headers
# ======================================================================

_USER_AGENTS = [
    # Chrome on Windows 11 (recent versions)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    # Chrome on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    # Edge on Windows (recent)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36 Edg/137.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0",
    # Firefox on Windows (recent)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:139.0) Gecko/20100101 Firefox/139.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:138.0) Gecko/20100101 Firefox/138.0",
    # Firefox on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:139.0) Gecko/20100101 Firefox/139.0",
    # Safari on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
]

_ACCEPT_LANGUAGES = [
    "en-US,en;q=0.9",
    "en-US,en;q=0.9,es;q=0.8",
    "en-GB,en;q=0.9,en-US;q=0.8",
    "en-US,en;q=0.9,fr;q=0.8",
    "en-US,en;q=0.9,de;q=0.8",
    "en,en-US;q=0.9",
]

# Pages visited during warmup to simulate organic browsing
_WARMUP_PATHS = [
    "/",
    "/markets/",
    "/markets/stocks-usa/",
    "/screener/",
    "/chart/",
]

def _get_random_headers() -> dict:
    """Generate realistic browser headers with randomized User-Agent."""
    ua = random.choice(_USER_AGENTS)
    headers = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": random.choice(_ACCEPT_LANGUAGES),
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }
    
    # Randomly add DNT header (~40% of users)
    if random.random() < 0.4:
        headers["DNT"] = "1"
    
    # Browser-specific headers
    if "Chrome" in ua:
        chrome_version = "137"
        if "Chrome/" in ua:
            chrome_version = ua.split("Chrome/")[1].split(".")[0]
        
        if "Edg" in ua:
            edge_version = ua.split("Edg/")[1].split(".")[0]
            headers["sec-ch-ua"] = f'"Microsoft Edge";v="{edge_version}", "Chromium";v="{chrome_version}", "Not?A_Brand";v="99"'
        else:
            headers["sec-ch-ua"] = f'"Chromium";v="{chrome_version}", "Google Chrome";v="{chrome_version}", "Not?A_Brand";v="99"'
        headers["sec-ch-ua-mobile"] = "?0"
        headers["sec-ch-ua-platform"] = '"Windows"' if "Windows" in ua else '"macOS"'
    
    return headers

# ======================================================================
# Shared constants / helpers for the BeautifulSoup + scanner API approach
# ======================================================================

# Deprecated: use _get_random_headers() instead
_HTTP_HEADERS = _get_random_headers()

_SCANNER_API_URL = "https://scanner.tradingview.com/america/scan"


def _scanner_api_fetch(pro_symbol: str, columns: list[str], session: _requests.Session = None) -> dict | None:
    """POST to TradingView scanner API and return a {column: value} dict."""
    payload = {
        "symbols": {"tickers": [pro_symbol], "query": {"types": []}},
        "columns": columns,
    }
    headers = _get_random_headers()
    headers["Content-Type"] = "application/json"
    headers["Origin"] = "https://www.tradingview.com"
    headers["Referer"] = "https://www.tradingview.com/"
    
    # Add small random delay before API call (0.5-2 seconds)
    time.sleep(random.uniform(0.5, 2.0))
    
    requester = session if session else _requests
    resp = requester.post(
        _SCANNER_API_URL,
        json=payload,
        headers=headers,
        timeout=15,
    )
    if resp.status_code != 200:
        return None
    result = resp.json()
    if result.get("error") or not result.get("data"):
        return None
    row = result["data"][0]["d"]
    return dict(zip(columns, row))


def _extract_pro_symbol(soup: BeautifulSoup) -> str | None:
    """Extract the pro_symbol (e.g. NASDAQ:AAPL) from embedded JSON metadata."""
    for script in soup.find_all("script"):
        txt = script.get_text().strip()
        if not (txt.startswith("{") and len(txt) > 5000):
            continue
        try:
            data = json.loads(txt)
        except (json.JSONDecodeError, ValueError):
            continue
        for _key, blob in data.items():
            if not isinstance(blob, dict):
                continue
            sym = blob.get("data", {}).get("symbol", {})
            if isinstance(sym, dict) and "pro_symbol" in sym:
                return sym["pro_symbol"]
    return None


# ======================================================================
# Overview helpers
# ======================================================================

_OVERVIEW_FUNDAMENTAL_FIELDS = [
    ("market_cap_basic", "Market Cap"),
    ("price_earnings_ttm", "P/E Ratio (TTM)"),
    ("earnings_per_share_basic_ttm", "EPS (TTM)"),
    ("dividends_yield", "Dividend Yield (%)"),
    ("total_revenue_fy", "Revenue (FY)"),
    ("net_income", "Net Income"),
    ("beta_1_year", "Beta (1Y)"),
    ("total_shares_outstanding", "Shares Outstanding"),
    ("float_shares_outstanding_current", "Float Shares"),
    ("number_of_employees", "Employees"),
    ("sector", "Sector"),
    ("industry", "Industry"),
    ("revenue_fq", "Revenue (Last Quarter)"),
    ("earnings_per_share_fq", "EPS (Last Quarter)"),
    ("earnings_fiscal_period_fq", "Earnings Period"),
    ("earnings_per_share_forecast_next_fq", "EPS Forecast (Next Q)"),
    ("revenue_forecast_next_fq", "Revenue Forecast (Next Q)"),
    ("earnings_release_next_date_fq", "Next Earnings Date"),
    ("recommendation_mark", "Analyst Rating (1=Strong Buy, 5=Strong Sell)"),
    ("all_time_high", "All-Time High"),
    ("all_time_high_day", "All-Time High Date"),
    ("all_time_low", "All-Time Low"),
    ("all_time_low_day", "All-Time Low Date"),
    ("fundamental_currency_code", "Currency"),
    ("web_site_url", "Website"),
]


def _format_overview_value(key: str, value):
    if value is None:
        return "N/A"
    if key == "dividends_yield":
        return f"{value:.2f}%"
    if key in ("market_cap_basic", "total_revenue_fy", "net_income", "revenue_fq",
               "revenue_forecast_next_fq"):
        if abs(value) >= 1e12:
            return f"${value / 1e12:.2f}T"
        if abs(value) >= 1e9:
            return f"${value / 1e9:.2f}B"
        if abs(value) >= 1e6:
            return f"${value / 1e6:.2f}M"
        return f"${value:,.0f}"
    if key in ("earnings_release_next_date_fq", "all_time_high_day", "all_time_low_day"):
        try:
            dt = datetime.fromtimestamp(value, tz=timezone.utc)
            return dt.strftime("%Y-%m-%d")
        except (ValueError, TypeError, OSError):
            return str(value)
    if key in ("price_earnings_ttm", "beta_1_year", "recommendation_mark"):
        return f"{value:.2f}"
    if key in ("earnings_per_share_basic_ttm", "earnings_per_share_fq",
               "earnings_per_share_forecast_next_fq"):
        return f"${value:.2f}"
    if key in ("total_shares_outstanding", "float_shares_outstanding_current",
               "number_of_employees"):
        return f"{value:,.0f}"
    if key in ("all_time_high", "all_time_low"):
        return f"${value:,.2f}"
    return str(value)


def _overview_try_html(soup: BeautifulSoup) -> dict | None:
    for tag in soup.find_all("h1"):
        if "fundamentals" in tag.get_text(strip=True).lower():
            parent = tag.parent
            return {"raw_content": parent.get_text(separator="\n", strip=True)}
    return None


def _overview_try_json(soup: BeautifulSoup) -> dict | None:
    for script in soup.find_all("script"):
        txt = script.get_text().strip()
        if not (txt.startswith("{") and len(txt) > 5000):
            continue
        try:
            data = json.loads(txt)
        except (json.JSONDecodeError, ValueError):
            continue
        for _key, blob in data.items():
            if not isinstance(blob, dict):
                continue
            sym = blob.get("data", {}).get("symbol", {})
            if not isinstance(sym, dict):
                continue
            if "market_cap_basic" not in sym or "price_earnings_ttm" not in sym:
                continue
            result = {
                "name": sym.get("short_description") or sym.get("description", ""),
                "ticker": sym.get("ticker_title") or sym.get("instrument_name", ""),
                "exchange": sym.get("exchange", ""),
            }
            fundamentals = {}
            for field_key, label in _OVERVIEW_FUNDAMENTAL_FIELDS:
                if field_key in sym:
                    fundamentals[field_key] = {
                        "label": label,
                        "value": sym[field_key],
                        "formatted": _format_overview_value(field_key, sym[field_key]),
                    }
            result["fundamentals"] = fundamentals
            return result
    return None


# ======================================================================
# Dividends helpers
# ======================================================================

_DIVIDEND_FIELDS = [
    ("dps_common_stock_prim_issue_fy", "Dividends Per Share (FY)"),
    ("dps_common_stock_prim_issue_fq", "Dividends Per Share (FQ)"),
    ("dividends_yield", "Dividend Yield (%)"),
    ("dividend_payout_ratio_ttm", "Payout Ratio (TTM %)"),
    ("dividend_payout_ratio_fy", "Payout Ratio (FY %)"),
    ("dps_common_stock_prim_issue_yoy_growth_fy", "DPS Growth YoY (FY %)"),
    ("continuous_dividend_payout", "Consecutive Years Paying"),
    ("continuous_dividend_growth", "Consecutive Years Growing"),
    ("ex_dividend_date_recent", "Ex-Dividend Date (Recent)"),
    ("dividends_per_share_fq", "Dividends Per Share (FQ, alt)"),
    ("earnings_per_share_basic_ttm", "EPS (TTM)"),
    ("price_earnings_ttm", "P/E Ratio (TTM)"),
    ("market_cap_basic", "Market Cap"),
    ("total_shares_outstanding", "Shares Outstanding"),
    ("fundamental_currency_code", "Currency"),
]
_DIVIDEND_SCANNER_COLS = [f for f, _ in _DIVIDEND_FIELDS] + [
    "description", "exchange", "name",
]


def _format_dividend_value(key: str, value):
    if value is None:
        return "N/A"
    if key in ("dividends_yield", "dividend_payout_ratio_ttm", "dividend_payout_ratio_fy",
               "dps_common_stock_prim_issue_yoy_growth_fy"):
        return f"{value:.2f}%"
    if key == "market_cap_basic":
        if abs(value) >= 1e12:
            return f"${value / 1e12:.2f}T"
        if abs(value) >= 1e9:
            return f"${value / 1e9:.2f}B"
        if abs(value) >= 1e6:
            return f"${value / 1e6:.2f}M"
        return f"${value:,.0f}"
    if key == "ex_dividend_date_recent":
        try:
            dt = datetime.fromtimestamp(value, tz=timezone.utc)
            return dt.strftime("%Y-%m-%d")
        except (ValueError, TypeError, OSError):
            return str(value)
    if key == "price_earnings_ttm":
        return f"{value:.2f}"
    if key in ("dps_common_stock_prim_issue_fy", "dps_common_stock_prim_issue_fq",
               "dividends_per_share_fq", "earnings_per_share_basic_ttm"):
        return f"${value:.2f}"
    if key == "total_shares_outstanding":
        return f"{value:,.0f}"
    if key in ("continuous_dividend_payout", "continuous_dividend_growth"):
        return f"{int(value)} years"
    return str(value)


def _build_dividend_dict(sym: dict) -> dict:
    result = {
        "name": sym.get("short_description") or sym.get("description", ""),
        "ticker": sym.get("ticker_title") or sym.get("instrument_name") or sym.get("name", ""),
        "exchange": sym.get("exchange", ""),
    }
    dividends = {}
    for field_key, label in _DIVIDEND_FIELDS:
        if field_key in sym and sym[field_key] is not None:
            dividends[field_key] = {
                "label": label,
                "value": sym[field_key],
                "formatted": _format_dividend_value(field_key, sym[field_key]),
            }
    result["dividends"] = dividends
    return result


def _dividends_try_html(soup: BeautifulSoup) -> dict | None:
    for tag in soup.find_all(["h1", "h2"]):
        text = tag.get_text(strip=True).lower()
        if "dividend" in text or "fundamentals" in text:
            parent = tag.parent
            return {"raw_content": parent.get_text(separator="\n", strip=True)}
    return None


def _dividends_try_json(soup: BeautifulSoup) -> dict | None:
    for script in soup.find_all("script"):
        txt = script.get_text().strip()
        if not (txt.startswith("{") and len(txt) > 5000):
            continue
        try:
            data = json.loads(txt)
        except (json.JSONDecodeError, ValueError):
            continue
        for _key, blob in data.items():
            if not isinstance(blob, dict):
                continue
            sym = blob.get("data", {}).get("symbol", {})
            if not isinstance(sym, dict):
                continue
            if "dividends_yield" not in sym and "dps_common_stock_prim_issue_fy" not in sym:
                continue
            return _build_dividend_dict(sym)
    return None


# ======================================================================
# Technicals helpers
# ======================================================================

_TECHNICALS_SCANNER_COLS = [
    "Recommend.All", "Recommend.Other", "Recommend.MA",
    "RSI", "RSI[1]",
    "Stoch.K", "Stoch.K[1]", "Stoch.D", "Stoch.D[1]",
    "CCI20", "CCI20[1]",
    "ADX", "ADX+DI", "ADX-DI", "ADX+DI[1]", "ADX-DI[1]",
    "AO", "AO[1]", "AO[2]",
    "Mom", "Mom[1]",
    "MACD.macd", "MACD.signal",
    "W.R",
    "BBPower",
    "UO",
    "EMA10", "SMA10", "EMA20", "SMA20", "EMA30", "SMA30",
    "EMA50", "SMA50", "EMA100", "SMA100", "EMA200", "SMA200",
    "Ichimoku.BLine", "VWMA", "HullMA9",
    "close",
    "description", "exchange", "name",
]

_OSCILLATOR_DISPLAY = [
    ("RSI", "RSI (14)"),
    ("Stoch.K", "Stochastic %K (14,3,3)"),
    ("CCI20", "CCI (20)"),
    ("ADX", "ADX (14)"),
    ("AO", "Awesome Oscillator"),
    ("Mom", "Momentum (10)"),
    ("MACD.macd", "MACD Level (12,26)"),
    ("W.R", "Williams %R (14)"),
    ("BBPower", "Bull Bear Power"),
    ("UO", "Ultimate Oscillator (7,14,28)"),
]

_MA_DISPLAY = [
    ("EMA10", "EMA (10)"), ("SMA10", "SMA (10)"),
    ("EMA20", "EMA (20)"), ("SMA20", "SMA (20)"),
    ("EMA30", "EMA (30)"), ("SMA30", "SMA (30)"),
    ("EMA50", "EMA (50)"), ("SMA50", "SMA (50)"),
    ("EMA100", "EMA (100)"), ("SMA100", "SMA (100)"),
    ("EMA200", "EMA (200)"), ("SMA200", "SMA (200)"),
    ("Ichimoku.BLine", "Ichimoku Base Line (9,26,52,26)"),
    ("VWMA", "VWMA (20)"), ("HullMA9", "Hull MA (9)"),
]


def _tech_recommendation_label(value) -> str:
    if value is None:
        return "N/A"
    if value >= 0.5:
        return "Strong Buy"
    if value > 0.1:
        return "Buy"
    if value >= -0.1:
        return "Neutral"
    if value > -0.5:
        return "Sell"
    return "Strong Sell"


def _oscillator_signal(key: str, sym: dict) -> str:
    v = sym.get(key)
    if v is None:
        return "Neutral"
    if key == "RSI":
        prev = sym.get("RSI[1]")
        if prev is not None:
            if v < 30 and v > prev:
                return "Buy"
            if v > 70 and v < prev:
                return "Sell"
        return "Neutral"
    if key == "Stoch.K":
        k, d = sym.get("Stoch.K"), sym.get("Stoch.D")
        k1, d1 = sym.get("Stoch.K[1]"), sym.get("Stoch.D[1]")
        if None not in (k, d, k1, d1):
            if k < 20 and d < 20 and k > d and k1 < d1:
                return "Buy"
            if k > 80 and d > 80 and k < d and k1 > d1:
                return "Sell"
        return "Neutral"
    if key == "CCI20":
        prev = sym.get("CCI20[1]")
        if prev is not None:
            if v < -100 and v > prev:
                return "Buy"
            if v > 100 and v < prev:
                return "Sell"
        return "Neutral"
    if key == "ADX":
        adx = sym.get("ADX")
        plus_di, minus_di = sym.get("ADX+DI"), sym.get("ADX-DI")
        plus_di1, minus_di1 = sym.get("ADX+DI[1]"), sym.get("ADX-DI[1]")
        if None not in (adx, plus_di, minus_di, plus_di1, minus_di1) and adx > 20:
            if plus_di > minus_di and plus_di1 < minus_di1:
                return "Buy"
            if plus_di < minus_di and plus_di1 > minus_di1:
                return "Sell"
        return "Neutral"
    if key == "AO":
        ao, ao1, ao2 = sym.get("AO"), sym.get("AO[1]"), sym.get("AO[2]")
        if None not in (ao, ao1):
            if (ao > 0 and ao1 < 0) or (ao2 is not None and ao > 0 and ao > ao1 and ao1 < ao2):
                return "Buy"
            if (ao < 0 and ao1 > 0) or (ao2 is not None and ao < 0 and ao < ao1 and ao1 > ao2):
                return "Sell"
        return "Neutral"
    if key == "Mom":
        prev = sym.get("Mom[1]")
        if prev is not None:
            return "Buy" if v > prev else ("Sell" if v < prev else "Neutral")
        return "Neutral"
    if key == "MACD.macd":
        signal = sym.get("MACD.signal")
        if signal is not None:
            return "Buy" if v > signal else ("Sell" if v < signal else "Neutral")
        return "Neutral"
    if key == "W.R":
        if v < -80:
            return "Buy"
        if v > -20:
            return "Sell"
        return "Neutral"
    if key == "BBPower":
        return "Buy" if v > 0 else ("Sell" if v < 0 else "Neutral")
    if key == "UO":
        if v < 30:
            return "Buy"
        if v > 70:
            return "Sell"
        return "Neutral"
    return "Neutral"


def _ma_signal(key: str, ma_val, close) -> str:
    if ma_val is None or close is None:
        return "Neutral"
    if close > ma_val:
        return "Buy"
    if close < ma_val:
        return "Sell"
    return "Neutral"


def _count_signals(signals: list[str]) -> tuple[int, int, int]:
    buy = sum(1 for s in signals if s == "Buy")
    sell = sum(1 for s in signals if s == "Sell")
    neutral = sum(1 for s in signals if s == "Neutral")
    return buy, sell, neutral


def _format_tech_value(key: str, value):
    if value is None:
        return "N/A"
    if key in ("RSI", "Stoch.K", "CCI20", "ADX", "W.R", "UO"):
        return f"{value:.2f}"
    if key in ("AO", "Mom", "MACD.macd", "BBPower"):
        return f"{value:.4f}"
    if key.startswith(("EMA", "SMA", "VWMA", "HullMA", "Ichimoku")):
        return f"${value:,.2f}"
    return str(value)


def _build_technicals_dict(sym: dict) -> dict:
    result = {
        "name": sym.get("short_description") or sym.get("description", ""),
        "ticker": sym.get("ticker_title") or sym.get("instrument_name") or sym.get("name", ""),
        "exchange": sym.get("exchange", ""),
        "price": sym.get("close"),
    }
    close = sym.get("close")
    osc_signals = [_oscillator_signal(k, sym) for k, _ in _OSCILLATOR_DISPLAY]
    ma_signals = [_ma_signal(k, sym.get(k), close) for k, _ in _MA_DISPLAY]
    all_signals = osc_signals + ma_signals
    total_buy, total_sell, total_neutral = _count_signals(all_signals)
    rec_all = sym.get("Recommend.All")
    result["summary"] = {
        "recommendation": {"value": rec_all, "label": _tech_recommendation_label(rec_all)} if rec_all is not None else None,
        "buy": total_buy, "sell": total_sell, "neutral": total_neutral,
    }
    osc_buy, osc_sell, osc_neutral = _count_signals(osc_signals)
    rec_osc = sym.get("Recommend.Other")
    osc_indicators = {}
    for (fk, label), sig in zip(_OSCILLATOR_DISPLAY, osc_signals):
        val = sym.get(fk)
        if val is not None:
            osc_indicators[fk] = {
                "label": label, "value": val,
                "formatted": _format_tech_value(fk, val), "signal": sig,
            }
    result["oscillators"] = {
        "recommendation": {"value": rec_osc, "label": _tech_recommendation_label(rec_osc)} if rec_osc is not None else None,
        "buy": osc_buy, "sell": osc_sell, "neutral": osc_neutral,
        "indicators": osc_indicators,
    }
    ma_buy, ma_sell, ma_neutral = _count_signals(ma_signals)
    rec_ma = sym.get("Recommend.MA")
    ma_indicators = {}
    for (fk, label), sig in zip(_MA_DISPLAY, ma_signals):
        val = sym.get(fk)
        if val is not None:
            ma_indicators[fk] = {
                "label": label, "value": val,
                "formatted": _format_tech_value(fk, val), "signal": sig,
            }
    result["moving_averages"] = {
        "recommendation": {"value": rec_ma, "label": _tech_recommendation_label(rec_ma)} if rec_ma is not None else None,
        "buy": ma_buy, "sell": ma_sell, "neutral": ma_neutral,
        "indicators": ma_indicators,
    }
    return result


def _technicals_try_html(soup: BeautifulSoup) -> dict | None:
    for tag in soup.find_all(["h1", "h2", "h3"]):
        text = tag.get_text(strip=True).lower()
        if "indicator" in text and "summary" in text:
            parent = tag.parent
            content = parent.get_text(separator="\n", strip=True)
            if len(content) > len(text) + 20:
                return {"raw_content": content}
    return None


def _technicals_try_json(soup: BeautifulSoup) -> dict | None:
    for script in soup.find_all("script"):
        txt = script.get_text().strip()
        if not (txt.startswith("{") and len(txt) > 5000):
            continue
        try:
            data = json.loads(txt)
        except (json.JSONDecodeError, ValueError):
            continue
        for _key, blob in data.items():
            if not isinstance(blob, dict):
                continue
            sym = blob.get("data", {}).get("symbol", {})
            if not isinstance(sym, dict):
                continue
            if "Recommend.All" not in sym and "RSI" not in sym:
                continue
            return _build_technicals_dict(sym)
    return None


# ======================================================================
# Forecast helpers
# ======================================================================

_FORECAST_SCANNER_COLS = [
    "price_target_average", "price_target_high", "price_target_low",
    "price_target_median",
    "recommendation_mark", "recommendation_buy", "recommendation_hold",
    "recommendation_sell", "recommendation_total",
    "Recommend.All",
    "close",
    "description", "exchange", "name",
]

_PRICE_TARGET_FIELDS = [
    ("price_target_average", "Average Price Target"),
    ("price_target_high", "High Price Target"),
    ("price_target_low", "Low Price Target"),
    ("price_target_median", "Median Price Target"),
]

_ANALYST_RATING_FIELDS = [
    ("recommendation_mark", "Overall Rating"),
    ("recommendation_total", "Total Analysts"),
    ("recommendation_buy", "Buy"),
    ("recommendation_hold", "Hold"),
    ("recommendation_sell", "Sell"),
]


def _forecast_recommendation_label(value) -> str:
    if value is None:
        return "N/A"
    if value <= 1.5:
        return "Strong Buy"
    if value <= 2.5:
        return "Buy"
    if value <= 3.5:
        return "Hold"
    if value <= 4.5:
        return "Sell"
    return "Strong Sell"


def _build_forecast_dict(sym: dict) -> dict:
    result = {
        "name": sym.get("short_description") or sym.get("description", ""),
        "ticker": sym.get("ticker_title") or sym.get("instrument_name") or sym.get("name", ""),
        "exchange": sym.get("exchange", ""),
        "current_price": sym.get("close"),
    }
    close = sym.get("close")
    price_target: dict = {}
    for field_key, label in _PRICE_TARGET_FIELDS:
        val = sym.get(field_key)
        if val is not None:
            price_target[field_key] = {
                "label": label, "value": val, "formatted": f"${val:,.2f}",
            }
    avg_target = sym.get("price_target_average")
    if avg_target is not None and close is not None and close > 0:
        upside_pct = ((avg_target - close) / close) * 100
        price_target["upside_pct"] = round(upside_pct, 2)
        price_target["upside_direction"] = "Upside" if upside_pct >= 0 else "Downside"
    result["price_target"] = price_target if price_target else None

    analyst_rating: dict = {}
    rec_mark = sym.get("recommendation_mark")
    if rec_mark is not None:
        analyst_rating["overall_rating"] = {
            "value": rec_mark,
            "label": _forecast_recommendation_label(rec_mark),
        }
    for field_key, label in _ANALYST_RATING_FIELDS:
        if field_key == "recommendation_mark":
            continue
        val = sym.get(field_key)
        if val is not None:
            analyst_rating[field_key] = {"label": label, "value": int(val)}
    buy = sym.get("recommendation_buy") or 0
    hold = sym.get("recommendation_hold") or 0
    sell = sym.get("recommendation_sell") or 0
    total = buy + hold + sell
    if total > 0:
        analyst_rating["distribution"] = {
            "buy_pct": round(buy / total * 100, 1),
            "hold_pct": round(hold / total * 100, 1),
            "sell_pct": round(sell / total * 100, 1),
        }
    rec_all = sym.get("Recommend.All")
    if rec_all is not None:
        analyst_rating["technical_recommendation"] = {
            "value": rec_all, "formatted": f"{rec_all:+.4f}",
        }
    result["analyst_rating"] = analyst_rating if analyst_rating else None
    return result


def _forecast_try_html(soup: BeautifulSoup) -> dict | None:
    for tag in soup.find_all(["h1", "h2", "h3"]):
        text = tag.get_text(strip=True).lower()
        if "price target" in text or "analyst rating" in text:
            parent = tag.parent
            content = parent.get_text(separator="\n", strip=True)
            if len(content) > 50:
                return {"raw_content": content}
    return None


def _forecast_try_json(soup: BeautifulSoup) -> dict | None:
    for script in soup.find_all("script"):
        txt = script.get_text().strip()
        if not (txt.startswith("{") and len(txt) > 5000):
            continue
        try:
            data = json.loads(txt)
        except (json.JSONDecodeError, ValueError):
            continue
        for _key, blob in data.items():
            if not isinstance(blob, dict):
                continue
            sym = blob.get("data", {}).get("symbol", {})
            if not isinstance(sym, dict):
                continue
            if "price_target_average" not in sym and "recommendation_mark" not in sym:
                continue
            return _build_forecast_dict(sym)
    return None


# ======================================================================
# TradingViewFetcher class
# ======================================================================

class TradingViewFetcher:
    """Hybrid fetcher: BS4 + scanner API for 4 resources, Playwright for options.
    
    Implements anti-bot detection measures:
    - Per-symbol session isolation (fresh session per fetch_all call)
    - Graduated 403 recovery with exponential backoff + session refresh
    - User-Agent rotation
    - Request timing randomization
    - Optional homepage warm-up for organic cookies
    """

    def __init__(self, request_delay_range: tuple = (1.0, 3.0),
                 max_403_retries: int = 3,
                 retry_delays: list = None,
                 warmup_enabled: bool = False):
        """Initialize fetcher with anti-bot measures.
        
        Args:
            request_delay_range: (min, max) seconds to wait between requests
            max_403_retries: Max 403 retry attempts with session refresh
            retry_delays: Backoff delays in seconds between retries
            warmup_enabled: Visit homepage before fetching to establish cookies
        """
        self._playwright = None
        self._browser = None
        self._session = _requests.Session()
        self._request_delay_range = request_delay_range
        self._last_request_time = 0
        self._max_403_retries = max_403_retries
        self._403_retry_delays = retry_delays or [10, 30, 90]
        self._warmup_enabled = warmup_enabled
        
        # Set initial headers for session
        self._session.headers.update(_get_random_headers())

    async def __aenter__(self):
        # Playwright is started lazily in _ensure_browser() only when needed
        return self

    async def __aexit__(self, *args):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        # Close requests session
        self._session.close()

    def _apply_rate_limiting(self):
        """Add random delay between requests to avoid bot detection."""
        elapsed = time.time() - self._last_request_time
        min_delay, max_delay = self._request_delay_range
        
        if elapsed < min_delay:
            wait_time = random.uniform(min_delay - elapsed, max_delay)
            logger.debug("Rate limiting: sleeping %.2f seconds", wait_time)
            time.sleep(wait_time)
        else:
            # Add small jitter even if enough time has passed
            jitter = random.uniform(0.2, 0.8)
            time.sleep(jitter)
        
        self._last_request_time = time.time()

    async def _ensure_browser(self):
        """Start Playwright + Chromium on first use (options chain only)."""
        if self._browser is None:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-blink-features=AutomationControlled",  # Hide automation
                    "--disable-dev-shm-usage",
                    "--disable-web-security",
                ],
            )

    # Retry delays in seconds for transient fetch failures (non-403)
    _RETRY_DELAYS = (5, 10)

    def _refresh_session(self):
        """Close old session and create a new one with fresh random headers."""
        self._session.close()
        self._session = _requests.Session()
        self._session.headers.update(_get_random_headers())
        logger.info("Session refreshed with new headers")

    async def _handle_403(self, resp, full_symbol: str, resource: str) -> str:
        """Graduated 403 recovery: jittered backoff + session refresh + warmup.
        
        Returns the successful response text, or raises HTTPError after
        all retries are exhausted.
        """
        url = resp.url
        for attempt in range(self._max_403_retries):
            delay_idx = min(attempt, len(self._403_retry_delays) - 1)
            base_delay = self._403_retry_delays[delay_idx]
            # Add ±30% jitter so retries aren't predictable
            delay = base_delay * random.uniform(0.7, 1.3)
            logger.warning(
                "403 on %s for %s — retry %d/%d in %.0fs with session refresh + warmup",
                resource, full_symbol, attempt + 1, self._max_403_retries, delay,
            )
            await asyncio.sleep(delay)
            self._refresh_session()

            # Warmup: visit a random TradingView page to establish cookies
            try:
                warmup_path = random.choice(_WARMUP_PATHS)
                warmup_headers = _get_random_headers()
                self._session.get(
                    f"https://www.tradingview.com{warmup_path}",
                    headers=warmup_headers,
                    timeout=15,
                )
                await asyncio.sleep(random.uniform(1.0, 3.0))
                logger.debug("403 retry warmup via %s completed", warmup_path)
            except Exception:
                logger.debug("403 retry warmup failed (non-fatal)")

            try:
                headers = _get_random_headers()
                # Vary Sec-Fetch-Site to look like internal navigation
                headers["Sec-Fetch-Site"] = "same-origin"
                headers["Referer"] = f"https://www.tradingview.com{random.choice(_WARMUP_PATHS)}"
                retry_resp = self._session.get(url, headers=headers, timeout=15)
                if retry_resp.status_code != 403:
                    retry_resp.raise_for_status()
                    logger.info("403 recovered for %s on attempt %d", full_symbol, attempt + 1)
                    return retry_resp.text
            except Exception as e:
                logger.warning("403 retry %d failed for %s: %s", attempt + 1, full_symbol, e)

        logger.error(
            "All %d 403 retries exhausted for %s (%s)",
            self._max_403_retries, full_symbol, resource,
        )
        resp.raise_for_status()

    async def _warmup(self):
        """Visit random TradingView pages to establish organic cookies."""
        try:
            # Visit 1-2 random pages for a more organic browsing pattern
            pages = random.sample(_WARMUP_PATHS, k=min(2, len(_WARMUP_PATHS)))
            for path in pages:
                headers = _get_random_headers()
                self._session.get(
                    f"https://www.tradingview.com{path}",
                    headers=headers,
                    timeout=15,
                )
                await asyncio.sleep(random.uniform(1.0, 3.0))
            logger.info("Homepage warm-up completed (%d pages)", len(pages))
        except Exception as e:
            logger.warning("Homepage warm-up failed (non-fatal): %s", e)

    async def _with_retry(self, fetch_coro_factory, label: str, _has_403: dict = None) -> str:
        """Call a fetch coroutine, retrying up to 2 times on non-403 errors.

        ``fetch_coro_factory`` is a no-arg callable that returns a new
        awaitable each time (needed because coroutines are single-use).
        """
        last_error = None
        for attempt in range(1 + len(self._RETRY_DELAYS)):
            try:
                result = await fetch_coro_factory()
                if result and not result.startswith("[ERROR:"):
                    return result
                last_error = result
            except HTTPError as e:
                # 403s are already retried by _handle_403; re-raise so
                # _timed_fetch can track the failure properly.
                if e.response is not None and e.response.status_code == 403:
                    raise
                last_error = f"[ERROR: {e}]"
                logger.warning(
                    "%s attempt %d failed: %s", label, attempt + 1, e,
                )
            except Exception as e:
                last_error = f"[ERROR: {e}]"
                logger.warning(
                    "%s attempt %d failed: %s", label, attempt + 1, e,
                )

            if attempt < len(self._RETRY_DELAYS):
                delay = self._RETRY_DELAYS[attempt]
                logger.info(
                    "Retrying %s in %ds (attempt %d/%d)",
                    label, delay, attempt + 2, 1 + len(self._RETRY_DELAYS),
                )
                await asyncio.sleep(delay)

        return last_error or "[ERROR: All retries exhausted]"

    # ------------------------------------------------------------------
    # BS4 fetchers (overview, technicals, forecast, dividends)
    # ------------------------------------------------------------------

    async def fetch_overview(self, full_symbol: str) -> str:
        """Fetch overview via requests + BeautifulSoup. Returns JSON string."""
        url = f"https://www.tradingview.com/symbols/{full_symbol}/"
        try:
            # Apply rate limiting before request
            self._apply_rate_limiting()
            
            headers = _get_random_headers()
            headers["Referer"] = "https://www.tradingview.com/"
            headers["Sec-Fetch-Site"] = "same-origin"
            
            resp = self._session.get(url, headers=headers, timeout=15)
            if resp.status_code == 403:
                resp_text = await self._handle_403(resp, full_symbol, "overview")
                soup = BeautifulSoup(resp_text, "html.parser")
            else:
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "html.parser")

            data = _overview_try_html(soup)
            if data:
                data["source"] = "html_extraction"
            else:
                data = _overview_try_json(soup)
                if data:
                    data["source"] = "embedded_json"

            if data is None:
                logger.warning("No overview data found for %s", full_symbol)
                return json.dumps({"title": "STOCK OVERVIEW", "symbol": full_symbol,
                                   "error": "Could not extract overview data from HTML or embedded JSON"})

            result = {
                "title": "STOCK OVERVIEW",
                "symbol": full_symbol,
                "name": data.get("name", ""),
                "ticker": data.get("ticker", ""),
                "exchange": data.get("exchange", ""),
                "source": data.get("source", "unknown"),
            }
            if "raw_content" in data:
                result["raw_content"] = data["raw_content"]
            elif "fundamentals" in data:
                result["fundamentals"] = data["fundamentals"]
            return json.dumps(result)

        except HTTPError:
            raise  # Let _timed_fetch handle 403s
        except Exception as e:
            logger.error("Failed to fetch overview for %s: %s", full_symbol, e)
            return json.dumps({"title": "STOCK OVERVIEW", "symbol": full_symbol,
                               "error": str(e)})

    async def fetch_technicals(self, full_symbol: str) -> str:
        """Fetch technicals via requests + BeautifulSoup + scanner API. Returns JSON string."""
        url = f"https://www.tradingview.com/symbols/{full_symbol}/technicals/"
        try:
            # Apply rate limiting before request
            self._apply_rate_limiting()
            
            headers = _get_random_headers()
            headers["Referer"] = f"https://www.tradingview.com/symbols/{full_symbol}/"
            headers["Sec-Fetch-Site"] = "same-origin"
            
            resp = self._session.get(url, headers=headers, timeout=15)
            if resp.status_code == 403:
                resp_text = await self._handle_403(resp, full_symbol, "technicals")
                soup = BeautifulSoup(resp_text, "html.parser")
            else:
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "html.parser")

            data = _technicals_try_html(soup)
            if data:
                data["source"] = "html_extraction"
            else:
                data = _technicals_try_json(soup)
                if data:
                    data["source"] = "embedded_json"
                else:
                    pro_symbol = _extract_pro_symbol(soup)
                    if pro_symbol:
                        sym = _scanner_api_fetch(pro_symbol, _TECHNICALS_SCANNER_COLS, self._session)
                        if sym:
                            data = _build_technicals_dict(sym)
                            data["source"] = "scanner_api"
                            data["pro_symbol"] = pro_symbol

            if data is None:
                logger.warning("No technicals data found for %s", full_symbol)
                return json.dumps({"title": "STOCK TECHNICALS", "symbol": full_symbol,
                                   "error": "Could not extract technicals data"})

            result = {
                "title": "STOCK TECHNICALS",
                "symbol": full_symbol,
                "name": data.get("name", ""),
                "ticker": data.get("ticker", ""),
                "exchange": data.get("exchange", ""),
                "source": data.get("source", "unknown"),
            }
            if data.get("price") is not None:
                result["price"] = data["price"]
            if "raw_content" in data:
                result["raw_content"] = data["raw_content"]
            else:
                for key in ("summary", "oscillators", "moving_averages"):
                    if key in data:
                        result[key] = data[key]
            if data.get("pro_symbol"):
                result["pro_symbol"] = data["pro_symbol"]
            return json.dumps(result)

        except HTTPError:
            raise  # Let _timed_fetch handle 403s
        except Exception as e:
            logger.error("Failed to fetch technicals for %s: %s", full_symbol, e)
            return json.dumps({"title": "STOCK TECHNICALS", "symbol": full_symbol,
                               "error": str(e)})

    async def fetch_forecast(self, full_symbol: str) -> str:
        """Fetch forecast via requests + BeautifulSoup + scanner API. Returns JSON string."""
        url = f"https://www.tradingview.com/symbols/{full_symbol}/forecast/"
        try:
            # Apply rate limiting before request
            self._apply_rate_limiting()
            
            headers = _get_random_headers()
            headers["Referer"] = f"https://www.tradingview.com/symbols/{full_symbol}/technicals/"
            headers["Sec-Fetch-Site"] = "same-origin"
            
            resp = self._session.get(url, headers=headers, timeout=15)
            if resp.status_code == 403:
                resp_text = await self._handle_403(resp, full_symbol, "forecast")
                soup = BeautifulSoup(resp_text, "html.parser")
            else:
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "html.parser")

            # Try scanner API first for structured data
            data = None
            pro_symbol = _extract_pro_symbol(soup)
            if pro_symbol:
                sym = _scanner_api_fetch(pro_symbol, _FORECAST_SCANNER_COLS, self._session)
                if sym:
                    data = _build_forecast_dict(sym)
                    data["source"] = "scanner_api"
                    data["pro_symbol"] = pro_symbol
            
            # Fall back to HTML extraction if scanner API failed
            if not data:
                data = _forecast_try_html(soup)
                if data:
                    data["source"] = "embedded_html"

            if data is None:
                logger.warning("No forecast data found for %s", full_symbol)
                return json.dumps({"title": "STOCK FORECAST", "symbol": full_symbol,
                                   "error": "Could not extract forecast data"})

            result = {
                "title": "STOCK FORECAST",
                "symbol": full_symbol,
                "name": data.get("name", ""),
                "ticker": data.get("ticker", ""),
                "exchange": data.get("exchange", ""),
                "source": data.get("source", "unknown"),
            }
            if data.get("current_price") is not None:
                result["current_price"] = data["current_price"]
            if "raw_content" in data:
                result["raw_content"] = data["raw_content"]
            else:
                for key in ("price_target", "analyst_rating"):
                    if key in data:
                        result[key] = data[key]
            if data.get("pro_symbol"):
                result["pro_symbol"] = data["pro_symbol"]
            return json.dumps(result)

        except HTTPError:
            raise  # Let _timed_fetch handle 403s
        except Exception as e:
            logger.error("Failed to fetch forecast for %s: %s", full_symbol, e)
            return json.dumps({"title": "STOCK FORECAST", "symbol": full_symbol,
                               "error": str(e)})

    async def fetch_dividends(self, full_symbol: str) -> str:
        """Fetch dividends via requests + BeautifulSoup + scanner API. Returns JSON string."""
        url = f"https://www.tradingview.com/symbols/{full_symbol}/financials-dividends/"
        try:
            # Apply rate limiting before request
            self._apply_rate_limiting()
            
            headers = _get_random_headers()
            headers["Referer"] = f"https://www.tradingview.com/symbols/{full_symbol}/forecast/"
            headers["Sec-Fetch-Site"] = "same-origin"
            
            resp = self._session.get(url, headers=headers, timeout=15)
            if resp.status_code == 403:
                resp_text = await self._handle_403(resp, full_symbol, "dividends")
                soup = BeautifulSoup(resp_text, "html.parser")
            else:
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "html.parser")

            # Try scanner API first for structured data
            data = None
            pro_symbol = _extract_pro_symbol(soup)
            if pro_symbol:
                sym = _scanner_api_fetch(pro_symbol, _DIVIDEND_SCANNER_COLS, self._session)
                if sym:
                    data = _build_dividend_dict(sym)
                    data["source"] = "scanner_api"
                    data["pro_symbol"] = pro_symbol
            
            # Fall back to HTML extraction if scanner API failed
            if not data:
                data = _dividends_try_html(soup)
                if data:
                    data["source"] = "embedded_html"

            if data is None:
                logger.warning("No dividends data found for %s", full_symbol)
                return json.dumps({"title": "STOCK DIVIDENDS", "symbol": full_symbol,
                                   "error": "Could not extract dividend data"})

            result = {
                "title": "STOCK DIVIDENDS",
                "symbol": full_symbol,
                "name": data.get("name", ""),
                "ticker": data.get("ticker", ""),
                "exchange": data.get("exchange", ""),
                "source": data.get("source", "unknown"),
            }
            if "raw_content" in data:
                result["raw_content"] = data["raw_content"]
            elif "dividends" in data:
                result["dividends"] = data["dividends"]
            if data.get("pro_symbol"):
                result["pro_symbol"] = data["pro_symbol"]
            return json.dumps(result)

        except HTTPError:
            raise  # Let _timed_fetch handle 403s
        except Exception as e:
            logger.error("Failed to fetch dividends for %s: %s", full_symbol, e)
            return json.dumps({"title": "STOCK DIVIDENDS", "symbol": full_symbol,
                               "error": str(e)})

    # ------------------------------------------------------------------
    # Options chain — Playwright (unchanged)
    # ------------------------------------------------------------------

    # Exact TradingView scanner endpoints that carry options chain data
    _OPTIONS_SCAN_URLS = [
        "scanner.tradingview.com/global/scan2?label-product=symbols-options",
        "scanner.tradingview.com/options/scan2?label-product=symbols-options",
    ]

    async def fetch_options_chain(self, full_symbol: str) -> str:
        """Fetch options chain by intercepting TradingView scanner API responses.

        Opens the options chain page and captures responses from the two known
        scanner endpoints. No clicking required.  Falls back to DOM innerText
        if no API data is captured.
        """
        await self._ensure_browser()
        url = f"https://www.tradingview.com/symbols/{full_symbol}/options-chain/"
        
        # Create new context with stealth settings for each page to avoid detection
        context = await self._browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent=random.choice(_USER_AGENTS),
            locale='en-US',
            timezone_id='America/New_York',
            # Add realistic permissions
            permissions=['geolocation'],
        )
        
        # Override navigator.webdriver to hide automation
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            // Add more realistic chrome object
            window.chrome = {
                runtime: {},
            };
            // Override permissions query to look more real
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
        """)
        
        page = await context.new_page()

        captured_responses: list[dict] = []

        async def _on_response(response):
            resp_url = response.url
            if not response.ok:
                return
            if not any(ep in resp_url for ep in self._OPTIONS_SCAN_URLS):
                return

            try:
                body = await response.text()
            except Exception:
                return

            captured_responses.append({
                "url": resp_url,
                "size": len(body),
                "body": body,
            })

        page.on("response", _on_response)

        try:
            # Add random delay before navigating (simulate human behavior)
            await asyncio.sleep(random.uniform(1.0, 2.5))
            
            await page.goto(url, wait_until="networkidle", timeout=45000)

            # Dismiss cookie / consent / login banners that may block rendering
            for selector in [
                '[class*="cookie"] button',
                '[class*="consent"] button',
                'button:has-text("Accept")',
                'button:has-text("OK")',
                'button:has-text("Got it")',
                'button:has-text("I agree")',
            ]:
                try:
                    btn = page.locator(selector).first
                    if await btn.is_visible(timeout=1000):
                        await btn.click()
                        # Add small delay after clicking (human-like)
                        await page.wait_for_timeout(random.randint(300, 800))
                except Exception:
                    pass

            # Extra wait for any async data loads after initial networkidle
            # Add randomization to make timing less predictable
            await page.wait_for_timeout(random.randint(2500, 4000))

            # -------------------------------------------------------
            # Build result from captured API responses
            # -------------------------------------------------------
            if captured_responses:
                parts: list[str] = [
                    f"OPTIONS CHAIN DATA (API intercepted, "
                    f"{len(captured_responses)} responses captured):\n"
                ]

                for idx, resp in enumerate(captured_responses, 1):
                    # Pretty-print JSON when possible
                    try:
                        parsed = json.loads(resp["body"])
                        parts.append(json.dumps(parsed, indent=2))
                    except (json.JSONDecodeError, ValueError):
                        parts.append(resp["body"])
                    parts.append("")  # blank separator

                logger.info(
                    "Captured %d API responses for options chain of %s",
                    len(captured_responses),
                    full_symbol,
                )
                return "\n".join(parts)

            # -------------------------------------------------------
            # Fallback: DOM innerText (old approach)
            # -------------------------------------------------------
            logger.warning(
                "No API responses captured for %s; falling back to DOM text",
                full_symbol,
            )
            page_text = await page.evaluate(
                '(() => { const m = document.querySelector("main") '
                '|| document.body; return m.innerText; })()'
            ) or ""

            if page_text:
                return (
                    "OPTIONS CHAIN DATA (FALLBACK — DOM innerText, "
                    "no API responses intercepted):\n" + page_text
                )
            return "[ERROR: No options chain data captured or rendered]"

        except Exception as e:
            logger.error(
                "Failed to fetch options chain for %s: %s", full_symbol, e,
            )
            return f"[ERROR: {e}]"
        finally:
            await page.close()
            await context.close()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def fetch_all(self, symbol: str, *,
                        force_refresh: bool = False,
                        cache: "tv_cache.TVCache | None" = None) -> dict:
        """Fetch all data for a symbol, with optional caching.

        Args:
            symbol: Hyphen-separated symbol (e.g. ``NYSE-MO``).
            force_refresh: When *True* always fetch from TradingView and
                update the cache.  When *False* (default) return cached
                data for each resource if available and not expired.
            cache: Optional :class:`tv_cache.TVCache` instance.  If *None*
                caching is disabled entirely (backward-compatible).

        Returns dict with keys: overview, technicals, forecast, dividends,
        options_chain, tv_403 (bool — True if ANY resource got 403),
        tv_403_resources (list of resource names that got 403), and
        cached_resources (list of resource names served from cache).
        Each resource is fetched independently — a 403 on one does NOT
        block the others.
        Timing stats are stored in ``self.last_fetch_stats``.
        """
        # Convert NYSE-MO → NYSE:MO for TradingView URLs
        full_symbol = symbol.replace("-", ":")

        logger.info("Pre-fetching TradingView data for %s (force_refresh=%s, cache=%s)",
                     symbol, force_refresh, "enabled" if cache else "disabled")

        self.last_fetch_stats: dict[str, dict] = {}
        # Track which individual resources hit unrecoverable 403
        _failed_resources: list[str] = []
        _cached_resources: list[str] = []

        # Optional homepage warm-up (skip if everything can come from cache)
        need_warmup = True

        # Helper that wraps a single fetch with timing + cache integration
        async def _timed_fetch(resource: str, factory, label: str) -> str:
            nonlocal need_warmup

            # --- cache read (when not forcing refresh) ---
            if cache and not force_refresh:
                async with cache.get_lock(symbol, resource):
                    entry = cache.get(symbol, resource)
                    if entry is not None:
                        _cached_resources.append(resource)
                        self.last_fetch_stats[resource] = {
                            **entry.fetch_stats,
                            "cached": True,
                            "cache_age": round(time.time() - entry.timestamp, 1),
                        }
                        logger.info("Cache hit for %s/%s (age %.1fs)",
                                    symbol, resource,
                                    time.time() - entry.timestamp)
                        return entry.data

            # --- warm-up (only once, only if we actually need to fetch) ---
            if need_warmup and self._warmup_enabled:
                await self._warmup()
                need_warmup = False

            # --- actual fetch ---
            has_error = False
            start = time.time()
            try:
                result = await self._with_retry(factory, label)
            except HTTPError as e:
                if e.response is not None and e.response.status_code == 403:
                    _failed_resources.append(resource)
                    logger.error("Unrecoverable 403 on %s for %s", resource, full_symbol)
                    result = f"No valid response for {resource} resource"
                    has_error = True
                else:
                    raise
            duration = time.time() - start
            stats = {
                "duration": round(duration, 2),
                "size": len(result),
                "error": has_error,
                "cached": False,
            }
            self.last_fetch_stats[resource] = stats

            # --- cache write (only on success) ---
            if cache and not has_error:
                cache.set(symbol, resource, result, stats)

            return result

        options_chain = await _timed_fetch(
            "options_chain",
            lambda fs=full_symbol: self.fetch_options_chain(fs),
            f"options_chain({symbol})",
        )
        logger.info("Options chain fetched: %d chars", len(options_chain))

        overview = await _timed_fetch(
            "overview",
            lambda fs=full_symbol: self.fetch_overview(fs),
            f"overview({symbol})",
        )
        logger.info("Overview fetched: %d chars", len(overview))

        technicals = await _timed_fetch(
            "technicals",
            lambda fs=full_symbol: self.fetch_technicals(fs),
            f"technicals({symbol})",
        )
        logger.info("Technicals fetched: %d chars", len(technicals))

        forecast = await _timed_fetch(
            "forecast",
            lambda fs=full_symbol: self.fetch_forecast(fs),
            f"forecast({symbol})",
        )
        logger.info("Forecast fetched: %d chars", len(forecast))

        dividends = await _timed_fetch(
            "dividends",
            lambda fs=full_symbol: self.fetch_dividends(fs),
            f"dividends({symbol})",
        )
        logger.info("Dividends fetched: %d chars", len(dividends))

        return {
            "overview": overview,
            "technicals": technicals,
            "forecast": forecast,
            "dividends": dividends,
            "options_chain": options_chain,
            "tv_403": len(_failed_resources) > 0,
            "tv_403_resources": _failed_resources,
            "cached_resources": _cached_resources,
        }


def create_fetcher(config=None):
    """Factory function to create TradingViewFetcher with config-based settings.
    
    Args:
        config: Optional Config object. If None, uses defaults.
        
    Returns:
        TradingViewFetcher instance with configured anti-403 settings.
    """
    if config is None:
        return TradingViewFetcher()
    
    return TradingViewFetcher(
        request_delay_range=(
            config.tradingview_request_delay_min,
            config.tradingview_request_delay_max,
        ),
        max_403_retries=config.tradingview_max_403_retries,
        retry_delays=config.tradingview_retry_delays,
        warmup_enabled=config.tradingview_warmup_enabled,
    )
