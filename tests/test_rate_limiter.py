"""Tests for the TokenBucket rate limiter."""

import time
import threading

import pytest

from openalex_neo4j.rate_limiter import TokenBucket


class TestTokenBucket:
    def test_acquire_basic(self):
        """Basic acquire reduces available tokens."""
        bucket = TokenBucket(rate=100, burst=100)
        start_tokens = bucket._tokens
        bucket.acquire()
        assert bucket._tokens == start_tokens - 1

    def test_acquire_multiple_tokens(self):
        """Acquire can consume multiple tokens at once."""
        bucket = TokenBucket(rate=100, burst=100)
        bucket.acquire(tokens=5)
        # Should have consumed 5 tokens
        assert bucket._tokens >= 90  # May have refilled slightly

    def test_bucket_refills(self):
        """Tokens are added over time."""
        bucket = TokenBucket(rate=100, burst=100)
        # Drain most tokens
        bucket.acquire(tokens=90)
        tokens_before = bucket._tokens
        time.sleep(0.05)
        bucket._refill()
        assert bucket._tokens > tokens_before

    def test_burst_limited(self):
        """Burst never exceeds the configured maximum."""
        bucket = TokenBucket(rate=1000, burst=20)
        # Wait for potential accumulation
        time.sleep(0.1)
        bucket._refill()
        assert bucket._tokens <= 20

    def test_acquire_waits_when_empty(self):
        """Acquire blocks when no tokens are available."""
        bucket = TokenBucket(rate=100, burst=1)
        bucket.acquire()  # Drain the single token
        start = time.monotonic()
        bucket.acquire()  # Should wait ~0.01s for 1 token at 100/s
        elapsed = time.monotonic() - start
        assert elapsed >= 0.005  # Should have waited at least a bit

    def test_thread_safety(self):
        """Multiple threads can share a bucket without exceeding the rate."""
        bucket = TokenBucket(rate=500, burst=50)
        errors = []

        def worker():
            for _ in range(20):
                bucket.acquire()

        threads = [threading.Thread(target=worker) for _ in range(5)]
        start = time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.monotonic() - start

        # 5 threads × 20 requests = 100 total
        # At 500 req/s, should take at least 100/500 = 0.2s
        # Allow some slack for scheduling
        assert elapsed >= 0.1

    def test_zero_rate(self):
        """Very low rate causes long waits."""
        bucket = TokenBucket(rate=1, burst=1)
        bucket.acquire()  # drain
        start = time.monotonic()
        bucket.acquire()  # must wait ~1s
        elapsed = time.monotonic() - start
        assert elapsed >= 0.5
