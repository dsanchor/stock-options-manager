"""TradingView data fetcher using native Python Playwright.

Pre-fetches overview, technicals, forecast, and options chain data from
TradingView before the agent runs. Returns clean text data the agent can
analyze without needing any browser tools.
"""

import asyncio
import json
import logging
import re
import time

from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)


class TradingViewFetcher:
    """Fetches TradingView data via headless Chromium (Python Playwright)."""

    def __init__(self):
        self._playwright = None
        self._browser = None

    async def __aenter__(self):
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        return self

    async def __aexit__(self, *args):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_page_text(self, url: str) -> str:
        """Navigate to *url* and return the ``#tv-content`` innerText."""
        page = await self._browser.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)
            text = await page.evaluate(
                '(() => { const el = document.getElementById("tv-content")'
                ' || document.querySelector("main") || document.body;'
                ' return el.innerText; })()'
            )
            return text or ""
        finally:
            await page.close()

    # Retry delays in seconds for transient fetch failures
    _RETRY_DELAYS = (5, 10)

    async def _with_retry(self, fetch_coro_factory, label: str) -> str:
        """Call a fetch coroutine, retrying up to 2 times on error.

        ``fetch_coro_factory`` is a no-arg callable that returns a new
        awaitable each time (needed because coroutines are single-use).
        """
        last_error = None
        for attempt in range(1 + len(self._RETRY_DELAYS)):
            try:
                result = await fetch_coro_factory()
                if result and not result.startswith("[ERROR:"):
                    return result
                # Treat an [ERROR:…] string as a soft failure worth retrying
                last_error = result
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
    # Page fetchers
    # ------------------------------------------------------------------

    async def fetch_overview(self, full_symbol: str) -> str:
        """Fetch overview page content from #tv-content."""
        url = f"https://www.tradingview.com/symbols/{full_symbol}/"
        try:
            text = await self._fetch_page_text(url)
            content = text or "[ERROR: No text content in overview response]"
            return f"STOCK OVERVIEW\n\n{content}"
        except Exception as e:
            logger.error("Failed to fetch overview for %s: %s", full_symbol, e)
            return f"STOCK OVERVIEW\n\n[ERROR: {e}]"

    async def fetch_technicals(self, full_symbol: str) -> str:
        """Fetch technicals page content from #tv-content."""
        url = f"https://www.tradingview.com/symbols/{full_symbol}/technicals/"
        try:
            text = await self._fetch_page_text(url)
            content = text or "[ERROR: No text content in technicals response]"
            return f"STOCK TECHNICALS\n\n{content}"
        except Exception as e:
            logger.error("Failed to fetch technicals for %s: %s", full_symbol, e)
            return f"STOCK TECHNICALS\n\n[ERROR: {e}]"

    async def fetch_forecast(self, full_symbol: str) -> str:
        """Fetch forecast page content from #tv-content."""
        url = f"https://www.tradingview.com/symbols/{full_symbol}/forecast/"
        try:
            text = await self._fetch_page_text(url)
            content = text or "[ERROR: No text content in forecast response]"
            return f"STOCK FORECAST\n\n{content}"
        except Exception as e:
            logger.error("Failed to fetch forecast for %s: %s", full_symbol, e)
            return f"STOCK FORECAST\n\n[ERROR: {e}]"

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
        url = f"https://www.tradingview.com/symbols/{full_symbol}/options-chain/"
        page = await self._browser.new_page()

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
                except Exception:
                    pass

            # Extra wait for any async data loads after initial networkidle
            await page.wait_for_timeout(3000)

            # -------------------------------------------------------
            # Build result from captured API responses
            # -------------------------------------------------------
            if captured_responses:
                parts: list[str] = [
                    f"OPTIONS CHAIN DATA (API intercepted, "
                    f"{len(captured_responses)} responses captured):\n"
                ]

                for idx, resp in enumerate(captured_responses, 1):
                    parts.append(
                        f"=== Response {idx}: {resp['url']} "
                        f"({resp['size']} bytes) ==="
                    )
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

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def fetch_all(self, symbol: str) -> dict:
        """Fetch all data for a symbol.

        Returns dict with keys: overview, technicals, forecast, options_chain.
        Timing stats are stored in ``self.last_fetch_stats``.
        """
        # Convert NYSE-MO → NYSE:MO for TradingView URLs
        full_symbol = symbol.replace("-", ":")

        logger.info("Pre-fetching TradingView data for %s", symbol)

        self.last_fetch_stats: dict[str, dict] = {}

        # Helper that wraps a single fetch with timing
        async def _timed_fetch(resource: str, factory, label: str) -> str:
            start = time.time()
            result = await self._with_retry(factory, label)
            duration = time.time() - start
            self.last_fetch_stats[resource] = {
                "duration": round(duration, 2),
                "size": len(result),
            }
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

        return {
            "overview": overview,
            "technicals": technicals,
            "forecast": forecast,
            "options_chain": options_chain,
        }
