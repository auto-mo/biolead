"""Concurrency control and rate limiting, per source.

Single-gene work never needed this: an assessment is four or five calls. A 506-gene list
is a different regime, and the failure mode is not slowness, it is being throttled halfway
through a run and reading the resulting errors as absence of evidence. That would turn a
transport problem into a scientific claim, which is the exact error this project exists to
prevent.

So every outbound call goes through a limiter owned by its source, not by the caller. The
single-gene path gets the same treatment as the batch path; there is no unlimited route.

Two mechanisms, because they bound different things:

  concurrency - an asyncio.Semaphore. Bounds how many requests are open at once, which is
                what protects the far end from a burst and protects us from opening 500
                sockets.
  rate        - a token bucket. Bounds requests per second averaged over time, which is
                what a published rate limit actually constrains. A semaphore alone does not
                bound rate: 8 concurrent 50ms calls is 160 requests per second.

Retries are here rather than in the clients because a retry that ignores the limiter is how
a throttled client turns into a hammering one. `RETRYABLE` is narrow: 429 and
5xx and transport errors, never a 4xx that means we asked wrong.
"""

from __future__ import annotations

import asyncio
import random
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import httpx


@dataclass
class LimiterStats:
    """Counters, surfaced to the client so throttling is visible rather than inferred."""

    calls: int = 0
    retries: int = 0
    throttled_seconds: float = 0.0
    failures: int = 0
    max_observed_concurrency: int = 0


class SourceLimiter:
    """One per source. Concurrency cap plus a token bucket, with bounded retry."""

    # Only these are retried. A 400 or a 404 means the request was wrong and repeating it
    # wastes the budget the limiter exists to protect.
    RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

    def __init__(
        self,
        name: str,
        *,
        max_concurrent: int,
        rate_per_second: float,
        burst: int | None = None,
        max_retries: int = 3,
    ) -> None:
        self.name = name
        self.max_concurrent = max_concurrent
        self.rate_per_second = rate_per_second
        self.burst = burst if burst is not None else max_concurrent
        self.max_retries = max_retries

        self._sem = asyncio.Semaphore(max_concurrent)
        self._tokens = float(self.burst)
        self._last = time.monotonic()
        self._bucket_lock = asyncio.Lock()
        self._in_flight = 0
        self.stats = LimiterStats()

    async def _take_token(self) -> None:
        """Block until a token is available. Refills continuously, not on a tick."""
        while True:
            async with self._bucket_lock:
                now = time.monotonic()
                self._tokens = min(
                    float(self.burst), self._tokens + (now - self._last) * self.rate_per_second
                )
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                # How long until one token exists. Computed inside the lock so two waiters
                # cannot both decide the wait is short and wake into the same empty bucket.
                wait = (1.0 - self._tokens) / self.rate_per_second
            self.stats.throttled_seconds += wait
            await asyncio.sleep(wait)

    @asynccontextmanager
    async def slot(self):
        """Hold one concurrency slot and one rate token for the duration of a call."""
        async with self._sem:
            await self._take_token()
            self._in_flight += 1
            self.stats.max_observed_concurrency = max(
                self.stats.max_observed_concurrency, self._in_flight
            )
            self.stats.calls += 1
            try:
                yield
            finally:
                self._in_flight -= 1

    async def run(self, fn, *args, **kwargs):
        """Call `fn` under the limiter, retrying only what is worth retrying.

        Backoff is exponential with jitter. The jitter is not decoration: without it, a
        batch that trips a rate limit retries in lockstep and trips it again together.
        """
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            async with self.slot():
                try:
                    return await fn(*args, **kwargs)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code not in self.RETRYABLE_STATUS:
                        self.stats.failures += 1
                        raise
                    last = exc
                    retry_after = _retry_after_seconds(exc.response)
                except (httpx.TransportError, httpx.HTTPError) as exc:
                    last = exc
                    retry_after = None

            if attempt == self.max_retries:
                break
            self.stats.retries += 1
            delay = retry_after if retry_after is not None else min(8.0, 0.5 * 2**attempt)
            await asyncio.sleep(delay + random.uniform(0, 0.25))

        self.stats.failures += 1
        assert last is not None
        raise last

    def snapshot(self) -> dict:
        return {
            "source": self.name,
            "max_concurrent": self.max_concurrent,
            "rate_per_second": self.rate_per_second,
            "calls": self.stats.calls,
            "retries": self.stats.retries,
            "failures": self.stats.failures,
            "throttled_seconds": round(self.stats.throttled_seconds, 2),
            "peak_concurrency": self.stats.max_observed_concurrency,
        }


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    """Honour Retry-After when the server sends one. Seconds form only."""
    raw = resp.headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, min(60.0, float(raw)))
    except ValueError:
        return None  # HTTP-date form; fall back to our own backoff rather than parse it


# --------------------------------------------------------------------------------------
# The registry. Numbers are set from what step zero measured, not from a published limit,
# because none of these four sources publishes one.
# --------------------------------------------------------------------------------------
#
#   open_targets      Batched. A 506-gene run is 2 calls (mapIds, then associatedTargets),
#                     so the cap here is generous and rarely reached. It bounds the
#                     per-gene enrichment path, which is the one that can still fan out.
#   clinicaltrials    Only fetched for genes carrying a curated NCT id, deduped by id.
#                     Single digits per run.
#   hpa               The one source with no batch route at all: multi-gene `search`
#                     returns [] with HTTP 200. Measured at ~0.5s per gene, so a 474-gene
#                     run is 474 calls and this limiter is what makes that survivable.
#   europepmc         Reserved. The client is designed and not implemented; the entry is
#                     here so the source appears in the stats table as unimplemented rather
#                     than being quietly absent from it.

LIMITS: dict[str, dict] = {
    "open_targets": {"max_concurrent": 6, "rate_per_second": 8.0, "burst": 12},
    # 2 concurrent / 2 per second, because ClinicalTrials.gov
    # returned sustained 429s. The limiter retried and the retries were refused too, so a
    # named trial id came back unreadable and a confidence band silently dropped. Their
    # published guidance is no fixed number, so this is set to something obviously polite
    # and the eval's degraded-run guard is what catches it if it is still too fast.
    "clinicaltrials": {"max_concurrent": 2, "rate_per_second": 2.0, "burst": 4},
    "hpa": {"max_concurrent": 6, "rate_per_second": 6.0, "burst": 10},
    "europepmc": {"max_concurrent": 4, "rate_per_second": 5.0, "burst": 8},
}


@dataclass
class LimiterRegistry:
    """Process-wide limiters, so two concurrent batch runs share one budget per source."""

    _limiters: dict[str, SourceLimiter] = field(default_factory=dict)

    def get(self, source: str) -> SourceLimiter:
        if source not in self._limiters:
            cfg = LIMITS.get(source, {"max_concurrent": 4, "rate_per_second": 4.0})
            self._limiters[source] = SourceLimiter(source, **cfg)
        return self._limiters[source]

    def snapshot(self) -> list[dict]:
        return [lim.snapshot() for lim in self._limiters.values()]


REGISTRY = LimiterRegistry()
