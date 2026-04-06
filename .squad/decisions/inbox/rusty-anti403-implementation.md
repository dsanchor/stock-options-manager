# Anti-403 Implementation — Rusty

**Date:** 2026-07-11  
**Status:** ✅ Implemented (all 4 phases)  
**Author:** Rusty (Agent Dev)

## Summary

Implemented Danny's 4-phase anti-403 architecture to make TradingView data fetching resilient against HTTP 403 rate-limiting blocks.

## Decisions Made

### Phase 1: Per-Symbol Session Isolation
- Moved `async with create_fetcher(config) as fetcher` **inside** the symbol loop in all 4 agent files
- Each symbol now gets a fresh HTTP session + Playwright browser lifecycle
- Monitor agents (open_call, open_put) scope the fetcher per-symbol, NOT per-position — positions within the same symbol share one fetcher since they fetch the same TradingView data
- Removed `self.has_403` instance flag; 403 state is now **local** to each `fetch_all()` call via a `_has_403` mutable dict
- `fetch_all()` returns `tv_403: bool` in the data dict so callers check the data, not the fetcher

### Phase 2: Graduated 403 Recovery
- Replaced `_check_403()` (which set a global flag and raised immediately) with `_handle_403()` — an async method that retries with exponential backoff (configurable, default 5s → 15s → 45s)
- Between each retry: closes old session, creates fresh `requests.Session` with new random headers
- After max retries exhausted, raises `HTTPError` which `fetch_all()` catches and marks `tv_403=True` for remaining resources
- `_with_retry()` still handles non-403 transient errors separately (5s, 10s delays)

### Phase 3: Symbol Randomization
- Added `random.shuffle(symbols_list)` in all 4 agent files, only when processing ALL symbols (not single-symbol runs)
- Guarded by `config.tradingview_randomize_symbols` (default: True)

### Phase 4: Homepage Warm-Up
- Added `_warmup()` async method that visits `https://www.tradingview.com/` to establish organic cookies
- Called at the start of `fetch_all()` when `warmup_enabled=True`
- Defaults to False (conservative)

## Files Modified

| File | Changes |
|------|---------|
| `config.yaml` | Added `warmup_enabled`, `max_403_retries`, `retry_delays`, `randomize_symbols` |
| `src/config.py` | 4 new config properties |
| `src/tv_data_fetcher.py` | Removed `has_403` flag, added `_handle_403()`, `_warmup()`, `_refresh_session()`, updated `fetch_all()` + `create_fetcher()` |
| `src/covered_call_agent.py` | Per-symbol fetcher + randomization |
| `src/cash_secured_put_agent.py` | Per-symbol fetcher + randomization |
| `src/open_call_monitor_agent.py` | Per-symbol fetcher + randomization |
| `src/open_put_monitor_agent.py` | Per-symbol fetcher + randomization |
| `src/agent_runner.py` | `data.get("tv_403")` instead of `fetcher.has_403` |
| `web/app.py` | `data.get("tv_403")` instead of `fetcher.has_403` |
