"""Simple TTL caches for API responses."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any

logger = logging.getLogger(__name__)


class TTLCache:
    """A minimal thread-safe TTL cache for JSON-serializable payloads."""

    def __init__(self, ttl_seconds: int = 300) -> None:
        """Initialize the cache with a fixed time-to-live."""

        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, tuple[Any, datetime]] = {}
        self._lock = RLock()

    def get(self, key: str) -> Any | None:
        """Return a cached value when it exists and has not expired."""

        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None

            value, stored_at = entry
            if datetime.now(timezone.utc) - stored_at > timedelta(seconds=self.ttl_seconds):
                del self._cache[key]
                logger.debug("TTL cache expired: %s", key)
                return None

            logger.debug("TTL cache hit: %s", key)
            return value

    def set(self, key: str, value: Any) -> None:
        """Store a value in the cache."""

        with self._lock:
            self._cache[key] = (value, datetime.now(timezone.utc))

    def invalidate(self, prefix: str | None = None) -> None:
        """Invalidate cached entries, optionally by key prefix."""

        with self._lock:
            if prefix is None:
                self._cache.clear()
                return

            for key in [key for key in self._cache if key.startswith(prefix)]:
                del self._cache[key]

    def clear(self) -> None:
        """Clear all cached entries."""

        self.invalidate()


library_cache = TTLCache(ttl_seconds=300)


def invalidate_library_cache() -> None:
    """Clear all cached library responses."""

    library_cache.invalidate("library:")
