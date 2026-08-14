"""Simple sliding-window rate limiter for dialogue API."""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Optional


class RateLimitError(Exception):
    """Raised when rate limit is exceeded."""

    def __init__(self, retry_after: float):
        super().__init__(f"Rate limit exceeded. Retry after {retry_after:.1f}s")
        self.retry_after = retry_after


class SlidingWindowRateLimiter:
    """In-memory sliding window rate limiter."""

    _MAX_KEYS = 10000

    def __init__(self, max_requests: int = 10, window_seconds: float = 60.0):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> Optional[float]:
        """Check if request is allowed. Returns retry_after if limited, None if ok."""
        now = time.monotonic()
        window_start = now - self.window_seconds

        # Evict stale keys if over limit
        if len(self._requests) > self._MAX_KEYS:
            stale = [k for k, v in self._requests.items() if not v or v[-1] < window_start]
            for k in stale[:len(stale) // 2]:
                del self._requests[k]

        # Clean old entries.
        self._requests[key] = [t for t in self._requests[key] if t > window_start]

        if len(self._requests[key]) >= self.max_requests:
            oldest = self._requests[key][0]
            return oldest + self.window_seconds - now

        return None

    def record(self, key: str) -> None:
        """Record a request."""
        self._requests[key].append(time.monotonic())

    def allow(self, key: str) -> None:
        """Check and record. Raises RateLimitError if limited."""
        retry_after = self.check(key)
        if retry_after is not None:
            raise RateLimitError(retry_after)
        self.record(key)


# Global instance.
_global_limiter = SlidingWindowRateLimiter(max_requests=10, window_seconds=60.0)


def get_rate_limiter() -> SlidingWindowRateLimiter:
    return _global_limiter
