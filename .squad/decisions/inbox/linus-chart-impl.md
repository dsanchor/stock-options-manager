# Decision: Price Chart Implementation on Symbol Detail Page

**Date:** 2025-07-25
**Author:** Linus (Quant Dev)
**Status:** Implemented

## Context
Added a candlestick price chart with decision/signal markers on the symbol detail page to provide a visual timeline of agent activity relative to price movements.

## Key Decisions

1. **Charting Library: TradingView Lightweight Charts (CDN)**
   - Apache 2.0, ~40KB, purpose-built for financial data
   - Loaded via CDN only in `symbol_detail.html`, NOT in `base.html`
   - No npm/bundler dependency needed

2. **Price Data: yfinance (3-month daily OHLC)**
   - Added `yfinance>=0.2.0` to `requirements.txt`
   - Runs in `asyncio.to_thread()` to avoid blocking the async event loop
   - Returns gracefully empty data on failure (chart shows "No price data available")

3. **Marker Data: CosmosDB decisions + signals**
   - Queries all agent types for the symbol (up to 50 decisions + 50 signals per type)
   - Signal markers (⚡ amber, aboveBar) vs decision markers (📊 gray, belowBar)
   - Clicking a marker navigates to `/decisions/{id}`

4. **New endpoint: `GET /api/symbols/{symbol}/chart-data`**
   - Returns `{"candles": [...], "markers": [...]}`
   - Markers sorted by time (Lightweight Charts requirement)

## Files Changed
- `web/app.py` — new `/api/symbols/{symbol}/chart-data` endpoint
- `web/templates/symbol_detail.html` — chart card + Lightweight Charts script
- `requirements.txt` — added `yfinance>=0.2.0`

## Impact
- Frontend only: no changes to agent logic, instructions, or CosmosDB schema
- Chart card appears between page header and Positions card
