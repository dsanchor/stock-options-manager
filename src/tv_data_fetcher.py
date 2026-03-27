"""TradingView data fetcher using Playwright MCP.

Pre-fetches overview, technicals, forecast, and options chain data from
TradingView before the agent runs. Returns clean text data the agent can
analyze without needing any browser tools.
"""

import asyncio
import logging
import os
import re

from agent_framework import MCPStdioTool

logger = logging.getLogger(__name__)


class TradingViewFetcher:
    """Fetches TradingView data via Playwright MCP container."""

    def __init__(self, command: str, args: list[str]):
        self.command = command
        self.args = args
        self._tool = None
        self._funcs = {}

    async def __aenter__(self):
        self._tool = MCPStdioTool(
            name="playwright",
            command=self.command,
            args=self.args,
            approval_mode="never_require",
            env=os.environ.copy(),
        )
        await self._tool.__aenter__()
        self._funcs = {f.name: f for f in self._tool.functions if hasattr(f, "name")}
        return self

    async def __aexit__(self, *args):
        if self._tool:
            await self._tool.__aexit__(*args)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_text(result) -> str:
        """Pull plain text out of an MCP tool result list."""
        for item in result:
            if hasattr(item, "text"):
                text = item.text
                if "### Result" in text:
                    text = text.split("### Result")[1].split("### Ran")[0].strip()
                    if text.startswith('"') and text.endswith('"'):
                        text = text[1:-1]
                return text
        return ""

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
        run_code = self._funcs.get("browser_run_code")
        if not run_code:
            return "[ERROR: browser_run_code tool not available]"

        url = f"https://www.tradingview.com/symbols/{full_symbol}/"
        js = (
            'async (page) => {'
            f'  await page.goto("{url}", {{ waitUntil: "networkidle" }});'
            '  await page.waitForTimeout(2000);'
            '  return await page.evaluate(() => {'
            '    const main = document.querySelector("main") || document.body;'
            '    return main.innerText;'
            '  });'
            '}'
        )

        try:
            result = await run_code(code=js)
            text = self._extract_text(result)
            return text or "[ERROR: No text content in overview response]"
        except Exception as e:
            logger.error("Failed to fetch overview for %s: %s", full_symbol, e)
            return f"[ERROR: {e}]"

    async def fetch_technicals(self, full_symbol: str) -> str:
        """Fetch technicals page innerText (~3 K chars)."""
        run_code = self._funcs.get("browser_run_code")
        if not run_code:
            return "[ERROR: browser_run_code tool not available]"

        url = f"https://www.tradingview.com/symbols/{full_symbol}/technicals/"
        js = (
            'async (page) => {'
            f'  await page.goto("{url}", {{ waitUntil: "networkidle" }});'
            '  await page.waitForTimeout(2000);'
            '  return await page.evaluate(() => {'
            '    const main = document.querySelector("main") || document.body;'
            '    return main.innerText;'
            '  });'
            '}'
        )

        try:
            result = await run_code(code=js)
            text = self._extract_text(result)
            return text or "[ERROR: No text content in technicals response]"
        except Exception as e:
            logger.error("Failed to fetch technicals for %s: %s", full_symbol, e)
            return f"[ERROR: {e}]"

    async def fetch_forecast(self, full_symbol: str) -> str:
        """Fetch forecast page innerText (~2.5 K chars)."""
        run_code = self._funcs.get("browser_run_code")
        if not run_code:
            return "[ERROR: browser_run_code tool not available]"

        url = f"https://www.tradingview.com/symbols/{full_symbol}/forecast/"
        js = (
            'async (page) => {'
            f'  await page.goto("{url}", {{ waitUntil: "networkidle" }});'
            '  await page.waitForTimeout(2000);'
            '  return await page.evaluate(() => {'
            '    const main = document.querySelector("main") || document.body;'
            '    return main.innerText;'
            '  });'
            '}'
        )

        try:
            result = await run_code(code=js)
            text = self._extract_text(result)
            return text or "[ERROR: No text content in forecast response]"
        except Exception as e:
            logger.error("Failed to fetch forecast for %s: %s", full_symbol, e)
            return f"[ERROR: {e}]"

    async def fetch_options_chain(self, full_symbol: str) -> str:
        """Fetch options chain: navigate, click best DTE row, snapshot.

        Uses browser_navigate (needs accessibility refs for clicking),
        then clicks the best expiration row (30-45 DTE), then snapshots.
        """
        nav = self._funcs.get("browser_navigate")
        click = self._funcs.get("browser_click")
        snapshot = self._funcs.get("browser_snapshot")

        if not all([nav, click, snapshot]):
            return "[ERROR: Required browser tools not available]"

        url = f"https://www.tradingview.com/symbols/{full_symbol}/options-chain/"

        try:
            # Step 1: Navigate to options chain
            result = await nav(url=url)
            snap_text = self._extract_text(result)

            if not snap_text:
                return "[ERROR: Empty options chain snapshot]"

            # Step 2: Find best expiration row (30-45 DTE)
            # Rows look like: row "April 24 29 DTE AAPL" [ref=e460]
            dte_pattern = r'row\s+"([^"]*?(\d+)\s+DTE[^"]*?)"\s+\[ref=([^\]]+)\]'
            matches = re.findall(dte_pattern, snap_text)

            if not matches:
                logger.warning("No DTE rows found in options chain for %s", full_symbol)
                return f"OPTIONS CHAIN (collapsed, no expandable rows found):\n{snap_text}"

            # Find closest to 30-45 DTE range
            best_match = None
            best_score = float("inf")
            for row_text, dte_str, ref in matches:
                dte = int(dte_str)
                if 30 <= dte <= 45:
                    score = 0
                else:
                    score = min(abs(dte - 30), abs(dte - 45))
                if score < best_score:
                    best_score = score
                    best_match = (row_text, dte, ref)

            if not best_match:
                return f"OPTIONS CHAIN (no suitable DTE found):\n{snap_text}"

            row_text, dte, ref = best_match
            logger.info(
                "Clicking expiration row: '%s' (%d DTE) ref=%s",
                row_text, dte, ref,
            )

            # Step 3: Click to expand
            await click(element=row_text, ref=ref)

            # Step 4: Snapshot expanded data
            result = await snapshot()
            expanded_text = self._extract_text(result)

            if expanded_text:
                return expanded_text
            return f"OPTIONS CHAIN (click succeeded but snapshot empty):\n{snap_text}"

        except Exception as e:
            logger.error("Failed to fetch options chain for %s: %s", full_symbol, e)
            return f"[ERROR: {e}]"

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

        # Fetch options chain FIRST — it uses browser_navigate + click +
        # snapshot (accessibility tree) which is fragile.  Running it on a
        # clean browser avoids state pollution from prior browser_run_code
        # calls.
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
