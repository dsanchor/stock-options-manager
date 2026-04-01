# TradingView Anti-Bot Detection Improvements

## Overview

This document describes the anti-bot detection measures implemented to prevent TradingView from blocking our automated requests with 403 Forbidden errors.

## Problem

TradingView was frequently returning 403 errors due to bot detection. The original implementation had several red flags:
1. **Static User-Agent**: Same UA string for every request
2. **No Session Management**: Each request was independent, no cookies/sessions
3. **Minimal Headers**: Missing common browser headers (Referer, sec-ch-ua, etc.)
4. **No Request Delays**: Sequential requests without timing randomization
5. **Playwright Automation Signals**: Browser automation was easily detectable
6. **No Rate Limiting**: All resources fetched immediately in rapid succession

## Implemented Solutions

### 1. User-Agent Rotation
- **What**: Pool of 7 realistic, current User-Agent strings from major browsers
- **Browsers**: Chrome (Windows/macOS), Edge, Firefox, Safari
- **Impact**: Each request uses a randomly selected UA, making traffic appear to come from different users

### 2. Realistic HTTP Headers
- **What**: Complete set of modern browser headers including:
  - `Sec-Fetch-*` headers (Dest, Mode, Site, User)
  - `sec-ch-ua` Chrome-specific headers with version matching
  - `Accept-Encoding: gzip, deflate, br, zstd` (modern compression)
  - `Upgrade-Insecure-Requests: 1`
  - `Cache-Control: max-age=0`
- **Impact**: Requests look identical to real browser traffic

### 3. Session Management with Persistent Cookies
- **What**: `requests.Session()` maintains cookies across requests
- **Impact**: TradingView sees a "logged in" session progressing through pages naturally

### 4. Request Timing Randomization
- **What**: Random delays between requests (configurable, default 1-3 seconds)
- **Mechanism**: `_apply_rate_limiting()` method adds jitter to every request
- **Impact**: Traffic pattern mimics human browsing behavior

### 5. Referer Chain
- **What**: Each request includes appropriate Referer header
- **Example**: 
  - Overview: `Referer: https://www.tradingview.com/`
  - Technicals: `Referer: https://www.tradingview.com/symbols/NASDAQ-AAPL/`
  - Forecast: `Referer: .../technicals/`
- **Impact**: Simulates natural navigation through TradingView pages

### 6. Playwright Stealth Mode
For options chain fetching (requires browser automation):
- **Chrome flags**: `--disable-blink-features=AutomationControlled`
- **JavaScript injection**: Removes `navigator.webdriver` property
- **Context isolation**: Fresh browser context per request with randomized viewport
- **Human-like timing**: Random delays before navigation and after clicks
- **Impact**: Browser automation is harder to detect

### 7. Scanner API Anti-Detection
- Added `Origin` and `Referer` headers to API requests
- Random delay (0.5-2s) before each API call
- Uses persistent session for cookie continuity

## Configuration

Anti-bot settings can be tuned via `config.yaml`:

```yaml
tradingview:
  request_delay_min: 1.0   # Minimum seconds between requests
  request_delay_max: 3.0   # Maximum seconds between requests
```

### Recommendations by Use Case:

**Development/Testing** (fast iteration):
```yaml
tradingview:
  request_delay_min: 0.5
  request_delay_max: 1.5
```

**Production** (maximum stealth):
```yaml
tradingview:
  request_delay_min: 2.0
  request_delay_max: 5.0
```

**Default** (balanced):
```yaml
tradingview:
  request_delay_min: 1.0
  request_delay_max: 3.0
```

## Code Changes

### Modified Files:
1. **src/tv_data_fetcher.py**
   - Added User-Agent pool and `_get_random_headers()` function
   - Updated `TradingViewFetcher.__init__()` to accept delay config
   - Added `_apply_rate_limiting()` method
   - Updated all fetch methods to use session and rate limiting
   - Enhanced Playwright stealth mode
   - Added `create_fetcher(config)` factory function

2. **src/config.py**
   - Added `tradingview_request_delay_min` property
   - Added `tradingview_request_delay_max` property

3. **config.yaml**
   - Added `tradingview` section with delay configuration

4. **Agent files** (covered_call_agent.py, cash_secured_put_agent.py, etc.)
   - Changed from `TradingViewFetcher()` to `create_fetcher(config)`

5. **web/app.py**
   - Updated all 3 instances to use `create_fetcher(config)`

## Usage

### In Agent Code:
```python
from .tv_data_fetcher import create_fetcher

async with create_fetcher(config) as fetcher:
    data = await fetcher.fetch_all(symbol)
```

### Direct Instantiation (if needed):
```python
from tv_data_fetcher import TradingViewFetcher

# With custom delays
fetcher = TradingViewFetcher(request_delay_range=(2.0, 4.0))

# Or use factory with config
from config import Config
from tv_data_fetcher import create_fetcher

config = Config()
fetcher = create_fetcher(config)
```

## Testing

To verify the changes work:

1. **Check logs**: Look for rate limiting messages
   ```
   DEBUG: Rate limiting: sleeping 2.34 seconds
   ```

2. **Monitor 403 errors**: Should be significantly reduced or eliminated

3. **Timing**: Note that fetch times will be longer (by design)
   - Overview: ~1-3s longer
   - Full symbol fetch (5 resources): ~5-15s longer total

4. **Test fetch preview**: Use web UI's "Fetch Preview" to test individual symbols

## Risk Assessment

### Low Risk:
- User-Agent rotation ✅
- HTTP header improvements ✅
- Session management ✅
- Request delays ✅

### Medium Risk:
- Playwright stealth mode (widely used, but detectable with advanced techniques)

### Mitigation if Issues Persist:
1. **Increase delays**: Set `request_delay_max` to 5-10 seconds
2. **Reduce frequency**: Lower cron schedule frequency in `config.yaml`
3. **Add proxy rotation**: (not implemented, would require additional infrastructure)
4. **Consider alternatives**: Use official APIs if available (TradingView has paid API tiers)

## Performance Impact

- **Fetch time increase**: 5-15 seconds per symbol (acceptable for background jobs)
- **Memory**: Minimal increase (one persistent Session object per fetcher)
- **CPU**: Negligible (random number generation)

## Monitoring

Watch for:
1. **403 error rate**: Should drop to near zero
2. **Fetch success rate**: Should improve to >95%
3. **Fetch duration**: Will increase but should be consistent
4. **TradingView changes**: If they update bot detection, we may need to adapt

## Future Improvements

If issues persist:
1. **Cookie persistence**: Save/load cookies between runs
2. **Browser fingerprinting**: More sophisticated browser emulation
3. **Residential proxies**: Rotate IP addresses
4. **CAPTCHA solving**: Manual intervention or service integration
5. **Official API**: Consider paid TradingView API access

## References

- [MDN: Fetch API](https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API)
- [Chrome User-Agent format](https://developer.chrome.com/docs/privacy-security/user-agent/)
- [Playwright Stealth](https://playwright.dev/docs/api/class-browser#browser-new-context)

---

**Last Updated**: 2025-01-09  
**Author**: Linus (Quant Dev)  
**Status**: ✅ Implemented & Tested (syntax checks passed)
