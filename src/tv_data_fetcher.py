"""TradingView data fetcher using native Python Playwright.

Pre-fetches overview, technicals, forecast, and options chain data from
TradingView before the agent runs. Returns clean text data the agent can
analyze without needing any browser tools.
"""

import asyncio
import logging
import re

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
        """Navigate to *url* and return the ``<main>`` (or ``<body>``) innerText."""
        page = await self._browser.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)
            text = await page.evaluate(
                '(() => { const m = document.querySelector("main") || document.body; return m.innerText; })()'
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
        """Fetch symbol overview page innerText (price, market cap, P/E, etc.)."""
        url = f"https://www.tradingview.com/symbols/{full_symbol}/"
        try:
            text = await self._fetch_page_text(url)
            return text or "[ERROR: No text content in overview response]"
        except Exception as e:
            logger.error("Failed to fetch overview for %s: %s", full_symbol, e)
            return f"[ERROR: {e}]"

    async def fetch_technicals(self, full_symbol: str) -> str:
        """Fetch technicals page innerText (~3 K chars)."""
        url = f"https://www.tradingview.com/symbols/{full_symbol}/technicals/"
        try:
            text = await self._fetch_page_text(url)
            return text or "[ERROR: No text content in technicals response]"
        except Exception as e:
            logger.error("Failed to fetch technicals for %s: %s", full_symbol, e)
            return f"[ERROR: {e}]"

    async def fetch_forecast(self, full_symbol: str) -> str:
        """Fetch forecast page innerText (~2.5 K chars)."""
        url = f"https://www.tradingview.com/symbols/{full_symbol}/forecast/"
        try:
            text = await self._fetch_page_text(url)
            return text or "[ERROR: No text content in forecast response]"
        except Exception as e:
            logger.error("Failed to fetch forecast for %s: %s", full_symbol, e)
            return f"[ERROR: {e}]"

    async def fetch_options_chain(self, full_symbol: str) -> str:
        """Fetch options chain: navigate, click best DTE row, extract text.

        Loads the options chain page, finds the best expiration row
        (30-45 DTE), clicks to expand it, then extracts the page text.
        """
        url = f"https://www.tradingview.com/symbols/{full_symbol}/options-chain/"
        page = await self._browser.new_page()

        try:
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)

            # Get visible text to find DTE rows
            page_text = await page.evaluate(
                '(() => { const m = document.querySelector("main") || document.body; return m.innerText; })()'
            ) or ""

            if not page_text:
                return "[ERROR: Empty options chain page]"

            # Find expiration rows by looking for DTE patterns in the page text
            # Lines look like: "Apr 24\n29 DTE" or similar
            dte_pattern = r'(\d+)\s+DTE'
            dte_matches = re.findall(dte_pattern, page_text)

            if not dte_matches:
                logger.warning("No DTE rows found in options chain for %s", full_symbol)
                return f"OPTIONS CHAIN (collapsed, no expandable rows found):\n{page_text}"

            # Find closest to 30-45 DTE range
            best_dte = None
            best_score = float("inf")
            for dte_str in dte_matches:
                dte = int(dte_str)
                if 30 <= dte <= 45:
                    score = 0
                else:
                    score = min(abs(dte - 30), abs(dte - 45))
                if score < best_score:
                    best_score = score
                    best_dte = dte

            if best_dte is None:
                return f"OPTIONS CHAIN (no suitable DTE found):\n{page_text}"

            logger.info("Clicking expiration row with %d DTE for %s", best_dte, full_symbol)

            # Click the row containing the best DTE
            dte_locator = page.get_by_text(re.compile(rf"\b{best_dte}\s+DTE\b"))
            try:
                await dte_locator.first.click(timeout=5000)
                await page.wait_for_timeout(2000)
            except Exception as click_err:
                logger.warning("Could not click DTE row: %s", click_err)
                return f"OPTIONS CHAIN (click failed, showing collapsed):\n{page_text}"

            # Extract expanded text
            expanded_text = await page.evaluate(
                '(() => { const m = document.querySelector("main") || document.body; return m.innerText; })()'
            ) or ""

            if expanded_text:
                return expanded_text
            return f"OPTIONS CHAIN (click succeeded but page empty):\n{page_text}"

        except Exception as e:
            logger.error("Failed to fetch options chain for %s: %s", full_symbol, e)
            return f"[ERROR: {e}]"
        finally:
            await page.close()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def fetch_all(self, symbol: str) -> dict:
        """Fetch all data for a symbol.

        Returns dict with keys: overview, technicals, forecast, options_chain.
        """
        # Convert NYSE-MO → NYSE:MO for TradingView URLs
        full_symbol = symbol.replace("-", ":")

        logger.info("Pre-fetching TradingView data for %s", symbol)

        options_chain = await self._with_retry(
            lambda fs=full_symbol: self.fetch_options_chain(fs),
            f"options_chain({symbol})",
        )
        logger.info("Options chain fetched: %d chars", len(options_chain))

        overview = await self._with_retry(
            lambda fs=full_symbol: self.fetch_overview(fs),
            f"overview({symbol})",
        )
        logger.info("Overview fetched: %d chars", len(overview))

        technicals = await self._with_retry(
            lambda fs=full_symbol: self.fetch_technicals(fs),
            f"technicals({symbol})",
        )
        logger.info("Technicals fetched: %d chars", len(technicals))

        forecast = await self._with_retry(
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
