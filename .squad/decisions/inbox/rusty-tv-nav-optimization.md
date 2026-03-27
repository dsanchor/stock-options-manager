# Decision: Remove Main Symbol Page from TradingView Agent Navigation

**Date:** 2025-07-16
**Author:** Rusty (Agent Dev)
**Status:** Implemented
**Impact:** Team-wide (changes agent data gathering behavior)

## Context

The TradingView Playwright agent was failing to gather data from technicals and forecast pages. Root cause: context window overflow. The 4 TradingView pages produce ~245K characters total via accessibility snapshots:
- Main symbol page: **103K chars** (the problem)
- Technicals: 48K chars
- Forecast: 29K chars
- Options chain (expanded): 65K chars

After loading main (103K) + options chain (65K) = 168K, there was no context room for technicals and forecast.

## Decision

Remove the main symbol page entirely from the data gathering protocol. Load only 3 pages in this order:
1. Technicals (48K) — smallest, most valuable for trading decisions
2. Forecast (29K) — earnings date, analyst consensus, price targets
3. Options chain (65K) — strikes, premiums, IV, Greeks

## Trade-offs

**Lost:**
- P/E, EPS, revenue, market cap, beta, company description not directly available
- CSP fundamental quality gate loses detailed financials

**Preserved/Replaced:**
- Current price → visible in options chain and forecast page headers
- Earnings date → available on forecast page
- Analyst targets → available on forecast page
- IV proxy (beta/volatility %) → replaced by actual IV% from expanded options chain (better!)
- CSP Investment Worthiness Gate → rewritten to use analyst consensus + earnings history

**Net impact:** Agent can now successfully load all 3 required pages and produce complete analyses. The lost fundamentals were nice-to-have but not critical for options timing decisions.

## Files Changed

- `src/tv_covered_call_instructions.py`
- `src/tv_cash_secured_put_instructions.py`
