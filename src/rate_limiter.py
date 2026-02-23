"""Per-user in-memory sliding-window rate limiter."""

import threading
import time


class RateLimiter:
    def __init__(self, max_requests: int = 30, window_seconds: float = 60.0):
        self._max = max_requests
        self._window = window_seconds
        self._lock = threading.Lock()
        self._requests: dict[str, list[float]] = {}  # user_key -> [timestamps]

    def check(self, user_key: str) -> bool:
        """Return True if request is allowed, False if rate-limited."""
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            timestamps = self._requests.get(user_key, [])
            timestamps = [t for t in timestamps if t > cutoff]
            if len(timestamps) >= self._max:
                self._requests[user_key] = timestamps
                return False
            timestamps.append(now)
            self._requests[user_key] = timestamps
            return True
