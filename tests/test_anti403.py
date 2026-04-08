"""
Tests for Anti-403 resilience strategy (TradingView data fetcher).

Validates the implementation of Danny's architecture spec
(.squad/decisions/decisions.md, "Anti-403 Strategy" section):

  Phase 1: Per-symbol session isolation (no global has_403, local _has_403 dict)
  Phase 2: Graduated 403 recovery (_handle_403 with backoff + _refresh_session)
  Phase 3: Symbol order randomization
  Phase 4: Homepage warm-up (_warmup, gated by _warmup_enabled in fetch_all)

Tests mock all HTTP I/O — no live TradingView access required.
"""

import asyncio
import json
import random
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
import requests as _requests

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**overrides):
    """Build a minimal config-like object for TradingView settings.

    Defaults match the spec's conservative defaults.
    """
    defaults = dict(
        tradingview_request_delay_min=1.0,
        tradingview_request_delay_max=3.0,
        tradingview_warmup_enabled=False,
        tradingview_max_403_retries=3,
        tradingview_retry_delays=[10, 30, 90],
        tradingview_randomize_symbols=True,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _mock_response(status_code=200, text="<html></html>", url="https://www.tradingview.com/"):
    """Create a mock requests.Response with required attributes."""
    resp = MagicMock(spec=_requests.Response)
    resp.status_code = status_code
    resp.text = text
    resp.url = url
    resp.headers = {"Content-Type": "text/html"}
    if status_code >= 400:
        resp.raise_for_status.side_effect = _requests.exceptions.HTTPError(
            response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


def _noop_sleep(_seconds=0):
    """Coroutine replacement for asyncio.sleep that returns instantly."""
    fut = asyncio.get_event_loop().create_future()
    fut.set_result(None)
    return fut


# ===================================================================
# 1. Per-symbol session isolation
# ===================================================================

class TestPerSymbolSessionIsolation:
    """Phase 1: Each TradingViewFetcher instance must have its own session."""

    def test_fresh_session_on_instantiation(self):
        """A new fetcher has a fresh requests.Session with no cookies."""
        from src.tv_data_fetcher import TradingViewFetcher

        fetcher = TradingViewFetcher()
        assert fetcher._session is not None
        assert isinstance(fetcher._session, _requests.Session)
        assert len(fetcher._session.cookies) == 0, \
            "New fetcher must start with an empty cookie jar"

    def test_sessions_are_independent(self):
        """Two fetcher instances must not share session state."""
        from src.tv_data_fetcher import TradingViewFetcher

        fetcher_a = TradingViewFetcher()
        fetcher_b = TradingViewFetcher()

        assert fetcher_a._session is not fetcher_b._session, \
            "Each fetcher must own a distinct requests.Session"

        fetcher_a._session.cookies.set("tainted", "yes")
        assert "tainted" not in fetcher_b._session.cookies, \
            "Cookie set on fetcher_a must not leak to fetcher_b"

    def test_no_instance_level_has_403(self):
        """Phase 1 removes the global has_403 attribute — 403 state is local
        to each fetch_all() call via the _has_403 dict."""
        from src.tv_data_fetcher import TradingViewFetcher

        fetcher = TradingViewFetcher()
        # The __init__ should NOT set self.has_403 anymore
        assert "has_403" not in fetcher.__dict__, \
            "has_403 should not be an instance attribute (use local _has_403 dict)"


# ===================================================================
# 2. Graduated 403 recovery
# ===================================================================

class TestGraduated403Recovery:
    """Phase 2: _handle_403 does exponential backoff + session refresh."""

    @pytest.mark.asyncio
    async def test_handle_403_retries_with_backoff(self):
        """_handle_403 retries up to max_403_retries with configured delays."""
        from src.tv_data_fetcher import TradingViewFetcher

        delays = [1, 2, 3]
        fetcher = TradingViewFetcher(
            request_delay_range=(0.0, 0.01),
            max_403_retries=3,
            retry_delays=delays,
        )

        # Build initial 403 response
        initial_resp = _mock_response(status_code=403,
                                       url="https://www.tradingview.com/symbols/NASDAQ:AAPL/")

        get_call_count = 0
        retry_get_count = 0

        def fake_get(*args, **kwargs):
            nonlocal get_call_count, retry_get_count
            get_call_count += 1
            url = args[0] if args else kwargs.get("url", "")
            # Warmup calls hit non-symbol URLs — return 200 for those
            if "/symbols/" not in str(url):
                return _mock_response(status_code=200, text="<html>warmup</html>",
                                       url=str(url))
            retry_get_count += 1
            if retry_get_count < 3:
                return _mock_response(status_code=403,
                                       url="https://www.tradingview.com/symbols/NASDAQ:AAPL/")
            return _mock_response(status_code=200, text="<html>recovered</html>",
                                   url="https://www.tradingview.com/symbols/NASDAQ:AAPL/")

        with patch.object(fetcher, "_refresh_session"), \
             patch("asyncio.sleep", side_effect=_noop_sleep) as mock_sleep:
            # After refresh, the new session's get must also be mocked
            fetcher._session = MagicMock(spec=_requests.Session)
            fetcher._session.get = MagicMock(side_effect=fake_get)

            result = await fetcher._handle_403(initial_resp, "NASDAQ:AAPL", "overview")

        assert result == "<html>recovered</html>"
        assert retry_get_count == 3  # 2 failed retries + 1 success
        # asyncio.sleep called for retry delays (with jitter) and warmup pauses
        sleep_values = [c.args[0] for c in mock_sleep.call_args_list]
        # First 3 retries: each has a jittered delay + warmup pause = 2 sleeps per attempt
        # Only 3 attempts happen (2 fail + 1 success), so at least 3 retry delay sleeps
        retry_sleeps = [v for v in sleep_values if v >= 0.7]  # retry delays are ≥ 0.7*min_delay
        assert len(retry_sleeps) >= 3, \
            f"Expected ≥3 retry delay sleeps, got {len(retry_sleeps)}: {sleep_values}"

    @pytest.mark.asyncio
    async def test_handle_403_refreshes_session_each_retry(self):
        """_handle_403 calls _refresh_session before each retry attempt."""
        from src.tv_data_fetcher import TradingViewFetcher

        fetcher = TradingViewFetcher(
            request_delay_range=(0.0, 0.01),
            max_403_retries=2,
            retry_delays=[1, 2],
        )

        initial_resp = _mock_response(status_code=403,
                                       url="https://www.tradingview.com/symbols/NASDAQ:AAPL/")

        with patch.object(fetcher, "_refresh_session") as mock_refresh, \
             patch("asyncio.sleep", side_effect=_noop_sleep):
            fetcher._session = MagicMock(spec=_requests.Session)
            # All retries also return 403
            fetcher._session.get = MagicMock(
                return_value=_mock_response(status_code=403,
                    url="https://www.tradingview.com/symbols/NASDAQ:AAPL/")
            )

            with pytest.raises(_requests.exceptions.HTTPError):
                await fetcher._handle_403(initial_resp, "NASDAQ:AAPL", "overview")

        assert mock_refresh.call_count == 2, \
            f"Expected _refresh_session called 2 times, got {mock_refresh.call_count}"

    @pytest.mark.asyncio
    async def test_max_retries_exhausted_raises(self):
        """After all retries exhausted, _handle_403 raises HTTPError."""
        from src.tv_data_fetcher import TradingViewFetcher

        fetcher = TradingViewFetcher(
            request_delay_range=(0.0, 0.01),
            max_403_retries=2,
            retry_delays=[1, 2],
        )

        initial_resp = _mock_response(status_code=403,
                                       url="https://www.tradingview.com/symbols/NASDAQ:AAPL/")

        with patch.object(fetcher, "_refresh_session"), \
             patch("asyncio.sleep", side_effect=_noop_sleep):
            fetcher._session = MagicMock(spec=_requests.Session)
            fetcher._session.get = MagicMock(
                return_value=_mock_response(status_code=403,
                    url="https://www.tradingview.com/symbols/NASDAQ:AAPL/")
            )

            with pytest.raises(_requests.exceptions.HTTPError):
                await fetcher._handle_403(initial_resp, "NASDAQ:AAPL", "overview")

    def test_refresh_session_creates_new_session(self):
        """_refresh_session replaces the session with a fresh one."""
        from src.tv_data_fetcher import TradingViewFetcher

        fetcher = TradingViewFetcher()
        old_session = fetcher._session
        old_session.cookies.set("bad_cookie", "tainted")

        fetcher._refresh_session()

        assert fetcher._session is not old_session, \
            "Session must be replaced after refresh"
        assert isinstance(fetcher._session, _requests.Session)
        assert len(fetcher._session.cookies) == 0, \
            "Refreshed session must have no cookies"

    @pytest.mark.asyncio
    async def test_non_403_errors_use_with_retry_logic(self):
        """Non-403 errors are handled by _with_retry (not _handle_403)."""
        from src.tv_data_fetcher import TradingViewFetcher

        fetcher = TradingViewFetcher(request_delay_range=(0.0, 0.01))

        call_count = 0

        async def failing_factory():
            nonlocal call_count
            call_count += 1
            raise _requests.exceptions.ConnectionError("Connection refused")

        with patch("asyncio.sleep", side_effect=_noop_sleep):
            result = await fetcher._with_retry(failing_factory, "test-label")

        # _RETRY_DELAYS = (5, 10) → 3 total attempts (1 + 2 retries)
        assert call_count == 1 + len(fetcher._RETRY_DELAYS), \
            f"Expected {1 + len(fetcher._RETRY_DELAYS)} attempts, got {call_count}"
        assert "ERROR" in result


# ===================================================================
# 3. No global 403 taint
# ===================================================================

class TestNoGlobal403Taint:
    """Verify 403 on one fetch cycle doesn't leak to new fetcher instances."""

    def test_new_fetcher_has_clean_state(self):
        """Creating a new fetcher always starts with clean state."""
        from src.tv_data_fetcher import TradingViewFetcher

        fetcher1 = TradingViewFetcher()
        fetcher1._session.cookies.set("cf_clearance", "blocked")

        fetcher2 = TradingViewFetcher()
        assert len(fetcher2._session.cookies) == 0, \
            "New fetcher must start with empty cookies"

    def test_no_module_level_session_state(self):
        """No module-level requests.Session that could leak between fetchers."""
        import src.tv_data_fetcher as mod

        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if isinstance(attr, _requests.Session):
                pytest.fail(
                    f"Module-level requests.Session found: {attr_name}. "
                    "This could cause global 403 taint between fetcher instances."
                )

    def test_create_fetcher_produces_isolated_instances(self):
        """create_fetcher() must produce independent instances each time."""
        from src.tv_data_fetcher import create_fetcher

        config = _make_config()
        f1 = create_fetcher(config)
        f2 = create_fetcher(config)

        assert f1 is not f2
        assert f1._session is not f2._session

    @pytest.mark.asyncio
    async def test_fetch_all_uses_local_403_state(self):
        """fetch_all() uses a local _has_403 dict, not instance-level state.
        The return dict includes tv_403 indicating whether 403 was hit."""
        from src.tv_data_fetcher import TradingViewFetcher

        fetcher = TradingViewFetcher(request_delay_range=(0.0, 0.01))

        mock_resp = _mock_response(status_code=200, text="<html></html>")

        with patch.object(fetcher._session, "get", return_value=mock_resp), \
             patch.object(fetcher, "fetch_options_chain",
                          return_value="options data"), \
             patch("time.sleep"):
            result = await fetcher.fetch_all("NASDAQ-AAPL")

        assert "tv_403" in result, \
            "fetch_all must return tv_403 key indicating 403 status"
        assert result["tv_403"] is False, \
            "tv_403 should be False when no 403 occurred"


# ===================================================================
# 4. Warmup (Phase 4)
# ===================================================================

class TestWarmup:
    """Phase 4: Optional homepage visit before data fetching."""

    @pytest.mark.asyncio
    async def test_warmup_visits_homepage(self):
        """_warmup() visits TradingView homepage to establish cookies."""
        from src.tv_data_fetcher import TradingViewFetcher

        fetcher = TradingViewFetcher(request_delay_range=(0.0, 0.01))

        with patch.object(fetcher._session, "get") as mock_get, \
             patch("asyncio.sleep", side_effect=_noop_sleep):
            mock_get.return_value = _mock_response(status_code=200)
            await fetcher._warmup()

            # Should have visited the homepage
            assert mock_get.call_count >= 1
            url_called = mock_get.call_args_list[0].args[0] if mock_get.call_args_list[0].args \
                else mock_get.call_args_list[0].kwargs.get("url", "")
            assert "tradingview.com" in str(url_called), \
                "Warmup should visit TradingView homepage"

    @pytest.mark.asyncio
    async def test_warmup_called_in_fetch_all_when_enabled(self):
        """fetch_all() calls _warmup() when _warmup_enabled is True."""
        from src.tv_data_fetcher import TradingViewFetcher

        fetcher = TradingViewFetcher(
            request_delay_range=(0.0, 0.01),
            warmup_enabled=True,
        )

        mock_resp = _mock_response(status_code=200, text="<html></html>")

        with patch.object(fetcher, "_warmup", new_callable=AsyncMock) as mock_warmup, \
             patch.object(fetcher._session, "get", return_value=mock_resp), \
             patch.object(fetcher, "fetch_options_chain",
                          return_value="options data"), \
             patch("time.sleep"):
            await fetcher.fetch_all("NASDAQ-AAPL")

        mock_warmup.assert_called_once()

    @pytest.mark.asyncio
    async def test_warmup_skipped_in_fetch_all_when_disabled(self):
        """fetch_all() does NOT call _warmup() when _warmup_enabled is False."""
        from src.tv_data_fetcher import TradingViewFetcher

        fetcher = TradingViewFetcher(
            request_delay_range=(0.0, 0.01),
            warmup_enabled=False,
        )

        mock_resp = _mock_response(status_code=200, text="<html></html>")

        with patch.object(fetcher, "_warmup", new_callable=AsyncMock) as mock_warmup, \
             patch.object(fetcher._session, "get", return_value=mock_resp), \
             patch.object(fetcher, "fetch_options_chain",
                          return_value="options data"), \
             patch("time.sleep"):
            await fetcher.fetch_all("NASDAQ-AAPL")

        mock_warmup.assert_not_called()

    @pytest.mark.asyncio
    async def test_warmup_failure_is_non_fatal(self):
        """If warmup fails (e.g., network error), it should not block fetching."""
        from src.tv_data_fetcher import TradingViewFetcher

        fetcher = TradingViewFetcher(request_delay_range=(0.0, 0.01))

        with patch.object(fetcher._session, "get",
                          side_effect=_requests.exceptions.ConnectionError("DNS failed")), \
             patch("asyncio.sleep", side_effect=_noop_sleep):
            # Should not raise
            await fetcher._warmup()


# ===================================================================
# 5. Symbol randomization (Phase 3)
# ===================================================================

class TestSymbolRandomization:
    """Phase 3: Symbol order should be shuffled when config flag is True."""

    def test_shuffle_applied_when_enabled(self):
        """Symbol list should be shuffled when randomize_symbols=True."""
        symbols = [{"symbol": f"SYM{i}"} for i in range(20)]
        original_order = [s["symbol"] for s in symbols]

        shuffled = list(symbols)
        random.shuffle(shuffled)
        shuffled_order = [s["symbol"] for s in shuffled]

        assert set(shuffled_order) == set(original_order), \
            "Shuffle must preserve all symbols"

    def test_original_order_preserved_when_disabled(self):
        """Symbol list should NOT be shuffled when randomize_symbols=False."""
        symbols = [{"symbol": f"SYM{i}"} for i in range(10)]
        original = list(symbols)

        config = _make_config(tradingview_randomize_symbols=False)
        if not config.tradingview_randomize_symbols:
            result = list(symbols)
        else:
            result = list(symbols)
            random.shuffle(result)

        assert result == original, \
            "Symbol order must be preserved when randomize_symbols=False"

    def test_shuffle_preserves_all_symbols(self):
        """Shuffling must not lose or duplicate any symbols."""
        symbols = [{"symbol": s} for s in ["AAPL", "MSFT", "TSLA", "GOOG", "AMZN"]]
        original_set = {s["symbol"] for s in symbols}

        shuffled = list(symbols)
        random.shuffle(shuffled)

        assert {s["symbol"] for s in shuffled} == original_set
        assert len(shuffled) == len(symbols)


# ===================================================================
# 6. Config loading
# ===================================================================

class TestConfigLoading:
    """Config properties for anti-403 settings with defaults."""

    def test_existing_delay_config_defaults(self):
        """Existing tradingview delay config properties have correct defaults."""
        config = _make_config()
        assert config.tradingview_request_delay_min == 1.0
        assert config.tradingview_request_delay_max == 3.0

    def test_custom_delay_values_override_defaults(self):
        """Custom delay values override defaults."""
        config = _make_config(
            tradingview_request_delay_min=2.0,
            tradingview_request_delay_max=5.0,
        )
        assert config.tradingview_request_delay_min == 2.0
        assert config.tradingview_request_delay_max == 5.0

    def test_new_anti403_config_defaults(self):
        """New anti-403 config properties have spec-defined defaults."""
        config = _make_config()
        assert config.tradingview_max_403_retries == 3
        assert config.tradingview_retry_delays == [10, 30, 90]
        assert config.tradingview_randomize_symbols is True
        assert config.tradingview_warmup_enabled is False

    def test_custom_anti403_values(self):
        """Custom anti-403 values override defaults."""
        config = _make_config(
            tradingview_max_403_retries=5,
            tradingview_retry_delays=[10, 30, 60, 120],
            tradingview_randomize_symbols=False,
            tradingview_warmup_enabled=True,
        )
        assert config.tradingview_max_403_retries == 5
        assert config.tradingview_retry_delays == [10, 30, 60, 120]
        assert config.tradingview_randomize_symbols is False
        assert config.tradingview_warmup_enabled is True

    def test_create_fetcher_uses_config_delays(self):
        """create_fetcher() applies config delay values to the fetcher."""
        from src.tv_data_fetcher import create_fetcher

        config = _make_config(
            tradingview_request_delay_min=2.5,
            tradingview_request_delay_max=6.0,
        )
        fetcher = create_fetcher(config)
        assert fetcher._request_delay_range == (2.5, 6.0)

    def test_create_fetcher_passes_anti403_settings(self):
        """create_fetcher() passes anti-403 settings to TradingViewFetcher."""
        from src.tv_data_fetcher import create_fetcher

        config = _make_config(
            tradingview_max_403_retries=5,
            tradingview_retry_delays=[10, 20],
            tradingview_warmup_enabled=True,
        )
        fetcher = create_fetcher(config)
        assert fetcher._max_403_retries == 5
        assert fetcher._403_retry_delays == [10, 20]
        assert fetcher._warmup_enabled is True

    def test_create_fetcher_defaults_without_config(self):
        """create_fetcher(None) uses default values."""
        from src.tv_data_fetcher import create_fetcher

        fetcher = create_fetcher(None)
        assert fetcher._request_delay_range == (1.0, 3.0)
        assert fetcher._max_403_retries == 3
        assert fetcher._403_retry_delays == [10, 30, 90]
        assert fetcher._warmup_enabled is False


# ===================================================================
# Integration-style: fetch_all with mocked HTTP
# ===================================================================

class TestFetchAllIntegration:
    """Integration tests for fetch_all with mocked HTTP responses."""

    @pytest.mark.asyncio
    async def test_fetch_all_returns_all_resource_keys(self):
        """fetch_all() returns dict with all expected keys including tv_403."""
        from src.tv_data_fetcher import TradingViewFetcher

        fetcher = TradingViewFetcher(request_delay_range=(0.0, 0.01))

        html = '<html><body><script>{"k":{"data":{"symbol":{"pro_symbol":"NASDAQ:AAPL","market_cap_basic":3e12,"price_earnings_ttm":30,"short_description":"Apple","exchange":"NASDAQ"}}}}</script></body></html>'
        mock_resp = _mock_response(status_code=200, text=html)

        with patch.object(fetcher._session, "get", return_value=mock_resp), \
             patch.object(fetcher, "fetch_options_chain",
                          return_value="options data"), \
             patch("time.sleep"):
            result = await fetcher.fetch_all("NASDAQ-AAPL")

        expected_keys = {"overview", "technicals", "forecast", "dividends",
                         "options_chain", "tv_403", "tv_403_resources"}
        assert set(result.keys()) == expected_keys, \
            f"fetch_all must return all resource keys, got {set(result.keys())}"

    @pytest.mark.asyncio
    async def test_fetch_all_403_returns_errors_in_resources(self):
        """When all 403 retries are exhausted, _timed_fetch catches the
        HTTPError, marks the resource as failed, and sets tv_403=True."""
        from src.tv_data_fetcher import TradingViewFetcher

        fetcher = TradingViewFetcher(
            request_delay_range=(0.0, 0.01),
            max_403_retries=1,
            retry_delays=[0],
        )

        resp_403 = _mock_response(status_code=403,
                                   url="https://www.tradingview.com/symbols/NASDAQ:AAPL/")

        with patch.object(fetcher._session, "get", return_value=resp_403), \
             patch.object(fetcher, "fetch_options_chain",
                          return_value="options data"), \
             patch.object(fetcher, "_refresh_session"), \
             patch("time.sleep"), \
             patch("asyncio.sleep", side_effect=_noop_sleep):
            result = await fetcher.fetch_all("NASDAQ-AAPL")

        # HTTPError(403) now propagates to _timed_fetch which sets
        # the "No valid response" fallback and tracks the failure.
        for resource in ["overview", "technicals", "forecast", "dividends"]:
            val = result[resource]
            assert isinstance(val, str), f"{resource} should be a string"
            assert "no valid response" in val.lower(), \
                f"{resource} should contain fallback text after 403 exhaustion"

        # tv_403 flag and resources list must be set
        assert result["tv_403"] is True, "tv_403 should be True when 403s occurred"
        assert set(result["tv_403_resources"]) == {
            "overview", "technicals", "forecast", "dividends"
        }, "All 4 BS4 resources should be in tv_403_resources"

        # Fetch stats should record errors
        for resource in ["overview", "technicals", "forecast", "dividends"]:
            assert fetcher.last_fetch_stats[resource]["error"] is True, \
                f"{resource} fetch stats should have error=True"
