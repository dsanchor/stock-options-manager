"""Tests for the TradingView in-memory cache (src/tv_cache.py)."""

import asyncio
import time

import pytest

from src.tv_cache import TVCache, CacheEntry, get_tv_cache, reset_tv_cache


# ---------------------------------------------------------------------------
# Basic get / set / TTL
# ---------------------------------------------------------------------------

class TestTVCache:
    def setup_method(self):
        self.cache = TVCache(ttl_overrides={"overview": 2, "technicals": 1})

    def test_get_empty(self):
        assert self.cache.get("NYSE-MO", "overview") is None

    def test_set_then_get(self):
        self.cache.set("NYSE-MO", "overview", '{"price": 42}', {"size": 14})
        entry = self.cache.get("NYSE-MO", "overview")
        assert entry is not None
        assert entry.data == '{"price": 42}'
        assert entry.fetch_stats == {"size": 14}

    def test_ttl_expiry(self):
        self.cache.set("NYSE-MO", "technicals", "data", {"size": 4})
        # Entry should be present immediately
        assert self.cache.get("NYSE-MO", "technicals") is not None
        # Expire it
        self.cache._store["NYSE-MO"]["technicals"].timestamp = time.time() - 2
        assert self.cache.get("NYSE-MO", "technicals") is None

    def test_get_all(self):
        self.cache.set("NYSE-MO", "overview", "ov", {"size": 2})
        self.cache.set("NYSE-MO", "technicals", "tech", {"size": 4})
        entries = self.cache.get_all("NYSE-MO")
        assert set(entries.keys()) == {"overview", "technicals"}

    def test_get_all_excludes_expired(self):
        self.cache.set("NYSE-MO", "overview", "ov", {"size": 2})
        self.cache.set("NYSE-MO", "technicals", "tech", {"size": 4})
        # Expire technicals
        self.cache._store["NYSE-MO"]["technicals"].timestamp = time.time() - 5
        entries = self.cache.get_all("NYSE-MO")
        assert "technicals" not in entries
        assert "overview" in entries

    def test_clear_symbol(self):
        self.cache.set("NYSE-MO", "overview", "ov", {"size": 2})
        self.cache.set("NYSE-AAPL", "overview", "ov2", {"size": 3})
        self.cache.clear("NYSE-MO")
        assert self.cache.get("NYSE-MO", "overview") is None
        assert self.cache.get("NYSE-AAPL", "overview") is not None

    def test_clear_all(self):
        self.cache.set("NYSE-MO", "overview", "ov", {"size": 2})
        self.cache.set("NYSE-AAPL", "overview", "ov2", {"size": 3})
        self.cache.clear_all()
        assert self.cache.stats()["total_entries"] == 0

    def test_stats(self):
        self.cache.set("NYSE-MO", "overview", "ov", {"size": 2})
        self.cache.set("NYSE-MO", "technicals", "tech", {"size": 4})
        s = self.cache.stats()
        assert s["total_entries"] == 2
        assert "NYSE-MO" in s["symbols"]


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

class TestSingleton:
    def setup_method(self):
        reset_tv_cache()

    def teardown_method(self):
        reset_tv_cache()

    def test_get_returns_same_instance(self):
        c1 = get_tv_cache()
        c2 = get_tv_cache()
        assert c1 is c2

    def test_reset_clears_instance(self):
        c1 = get_tv_cache()
        reset_tv_cache()
        c2 = get_tv_cache()
        assert c1 is not c2


# ---------------------------------------------------------------------------
# Async lock (single-flight)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lock_prevents_concurrent_fetch():
    """Two coroutines competing for the same key — the lock serializes them."""
    cache = TVCache()
    lock = cache.get_lock("NYSE-MO", "overview")
    order: list[str] = []

    async def worker(name: str):
        async with lock:
            order.append(f"{name}-enter")
            await asyncio.sleep(0.05)
            order.append(f"{name}-exit")

    await asyncio.gather(worker("A"), worker("B"))
    # One must fully complete before the other starts
    assert order[0].endswith("-enter")
    assert order[1].endswith("-exit")
    assert order[2].endswith("-enter")
    assert order[3].endswith("-exit")
