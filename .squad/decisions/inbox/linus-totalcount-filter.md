# Decision: Filter TradingView Scanner Responses by totalCount

**Date:** 2026-07-25  
**Author:** Linus (Quant Dev)  
**Status:** Implemented  
**Impact:** Data quality for options chain fetching

## Context

The `fetch_options_chain` method intercepts TradingView scanner API responses matching `_OPTIONS_SCAN_URLS`. One of the intercepted responses contains `totalCount: 1` — it's metadata/noise, not actual option chain data. This polluted the captured data sent downstream.

## Decision

Filter responses at capture time inside `_on_response`: parse the JSON body and discard any response where `totalCount <= 1`. Responses that fail JSON parsing are kept (safe default).

## Rationale

- Filtering at the callback level prevents garbage from ever entering `captured_responses`
- `totalCount > 1` is a reliable discriminator: real option chain data always has multiple rows
- Non-JSON responses are allowed through as a safe fallback (shouldn't happen for these endpoints, but defensive)

## Files Modified

- `src/tv_data_fetcher.py` — `_on_response` callback in `fetch_options_chain`
