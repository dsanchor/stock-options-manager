"""In-memory cache for TradingView fetch results.

Stores the last successful fetch per (symbol, resource) pair.
Failed fetches are never cached so the next request retries automatically.

Thread-safe under asyncio via per-key locks that prevent stampede
(multiple concurrent cache-miss fetches for the same resource).
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

RESOURCES = ("overview", "technicals", "forecast", "dividends", "options_chain")

# Default TTL per resource (seconds).  Options chains are now populated
# by a scheduled fetcher (every hour), so we give them longer TTL.
# Overview / dividends are more stable.
DEFAULT_TTL: dict[str, int] = {
    "options_chain": 7200,    # 2 hours (scheduler runs hourly)
    "technicals": 600,        # 10 min
    "forecast": 600,          # 10 min
    "overview": 1800,         # 30 min
    "dividends": 3600,        # 1 hour
}


@dataclass
class CacheEntry:
    data: str
    fetch_stats: dict
    timestamp: float = field(default_factory=time.time)


class TVCache:
    """Process-local TradingView data cache.

    Keyed by ``(symbol, resource)`` where *symbol* uses the hyphen
    format (e.g. ``NYSE-MO``) and *resource* is one of :data:`RESOURCES`.
    """

    def __init__(self, ttl_overrides: dict[str, int] | None = None):
        self._store: dict[str, dict[str, CacheEntry]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._ttl = {**DEFAULT_TTL, **(ttl_overrides or {})}

    # -- helpers ----------------------------------------------------------

    def _lock_key(self, symbol: str, resource: str) -> str:
        return f"{symbol}::{resource}"

    def get_lock(self, symbol: str, resource: str) -> asyncio.Lock:
        """Return (or create) the asyncio Lock for a (symbol, resource) pair."""
        key = self._lock_key(symbol, resource)
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    # -- public API -------------------------------------------------------

    def get(self, symbol: str, resource: str) -> Optional[CacheEntry]:
        """Return cached entry if present and not expired, else None."""
        entry = self._store.get(symbol, {}).get(resource)
        if entry is None:
            return None
        ttl = self._ttl.get(resource, 600)
        if time.time() - entry.timestamp > ttl:
            logger.debug("Cache expired for %s/%s", symbol, resource)
            self._store[symbol].pop(resource, None)
            return None
        return entry

    def set(self, symbol: str, resource: str, data: str,
            fetch_stats: dict) -> None:
        """Store a successful fetch result."""
        self._store.setdefault(symbol, {})[resource] = CacheEntry(
            data=data, fetch_stats=fetch_stats,
        )
        logger.debug("Cached %s/%s (%d chars)", symbol, resource, len(data))

    def get_all(self, symbol: str) -> dict[str, CacheEntry]:
        """Return all non-expired cached entries for a symbol."""
        result = {}
        for res in RESOURCES:
            entry = self.get(symbol, res)
            if entry is not None:
                result[res] = entry
        return result

    def clear(self, symbol: str) -> None:
        """Drop all cached entries for a symbol."""
        self._store.pop(symbol, None)

    def clear_all(self) -> None:
        """Drop the entire cache."""
        self._store.clear()

    def stats(self) -> dict:
        """Return cache statistics for debugging."""
        total = sum(len(v) for v in self._store.values())
        symbols = list(self._store.keys())
        return {"total_entries": total, "symbols": symbols}


# -- module-level singleton ------------------------------------------------

_default_cache: TVCache | None = None


def get_tv_cache() -> TVCache:
    """Return the process-wide default cache instance."""
    global _default_cache
    if _default_cache is None:
        _default_cache = TVCache()
    return _default_cache


def reset_tv_cache() -> None:
    """Reset the global cache (useful for tests)."""
    global _default_cache
    _default_cache = None
