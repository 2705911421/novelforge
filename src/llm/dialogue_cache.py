"""Simple LRU + TTL cache for dialogue generation results."""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from typing import Any, Optional


class DialogueCache:
    """In-memory LRU cache with TTL for dialogue results."""

    def __init__(self, max_size: int = 100, ttl_seconds: float = 3600.0):
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def _make_key(self, **kwargs: Any) -> str:
        """Create a cache key from request parameters."""
        raw = json.dumps(kwargs, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, **kwargs: Any) -> Optional[Any]:
        """Get cached result if exists and not expired."""
        key = self._make_key(**kwargs)
        if key not in self._cache:
            return None

        timestamp, value = self._cache[key]
        if time.monotonic() - timestamp > self.ttl_seconds:
            del self._cache[key]
            return None

        # Move to end (most recently used).
        self._cache.move_to_end(key)
        return value

    def set(self, value: Any, **kwargs: Any) -> None:
        """Store a result in cache."""
        key = self._make_key(**kwargs)

        # Evict oldest if at capacity.
        while len(self._cache) >= self.max_size:
            self._cache.popitem(last=False)

        self._cache[key] = (time.monotonic(), value)

    def clear(self) -> None:
        """Clear all cached entries."""
        self._cache.clear()

    @property
    def size(self) -> int:
        return len(self._cache)


# Global instance.
_global_cache = DialogueCache(max_size=100, ttl_seconds=3600.0)


def get_dialogue_cache() -> DialogueCache:
    return _global_cache
