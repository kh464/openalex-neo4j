"""Token bucket rate limiter for OpenAlex polite pool."""

import time
import threading


class TokenBucket:
    """Thread-safe token bucket rate limiter.

    OpenAlex polite pool allows ~10 requests/second.  Use this to
    throttle concurrent requests so they don't trigger HTTP 429.

    Example::

        bucket = TokenBucket(rate=10, burst=10)
        bucket.acquire()   # blocks until a token is available
        response = requests.get(...)

    Thread-safe: multiple workers can share one bucket.
    """

    def __init__(self, rate: float = 10, burst: int = 10):
        """Initialize token bucket.

        Args:
            rate:  Tokens added per second (max sustained request rate).
            burst: Maximum number of tokens that can accumulate.
        """
        self.rate = rate
        self.burst = burst
        self._tokens = burst
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, tokens: float = 1) -> None:
        """Wait until *tokens* are available, then consume them.

        Blocks the calling thread if the bucket is empty.
        """
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                need = tokens - self._tokens
                wait = need / self.rate
            time.sleep(max(wait, 0.01))

    def _refill(self) -> None:
        """Refill tokens according to elapsed time."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
        self._last_refill = now
