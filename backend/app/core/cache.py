"""An async cache with single-flight, keyed on the data snapshot.

Two distinct wins, and only one of them is the obvious one.

The obvious one: the borrow lookup and the condition resolution are identical for every
gene in a run. Resolving them 506 times would be 506 identical calls to answer a question
whose answer cannot change within a run.

The one that actually matters: SINGLE-FLIGHT. Without it, a fan-out of 500 coroutines that
all want the same key at the same moment produces 500 concurrent misses before the first
one finishes writing. A plain TTL cache does not help there, because the window where the
value is absent is exactly the window where every caller is looking. So a key in flight
gets a future to await rather than a second call.

Keys carry the Open Targets data version. Determinism in this project is only ever claimed
against a pinned snapshot, and a cache that outlived a data release would quietly serve
answers from one snapshot alongside answers from another.

Negative results ARE cached, with one exception that matters: `could_not_check` is never
cached. "We looked and there was nothing" is a finding and caching it is correct. "We could
not look" is a transport failure, and caching that would freeze a temporary outage into the
run's conclusions.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    joins: int = 0          # callers that waited on someone else's in-flight call
    uncacheable: int = 0    # results refused by the cacheable predicate
    evictions: int = 0

    def as_dict(self) -> dict:
        total = self.hits + self.misses + self.joins
        return {
            "hits": self.hits,
            "misses": self.misses,
            "single_flight_joins": self.joins,
            "uncacheable": self.uncacheable,
            "lookups": total,
            "hit_rate": round((self.hits + self.joins) / total, 3) if total else None,
        }


@dataclass
class _Entry:
    value: Any
    expires_at: float


class AsyncCache:
    """TTL cache with single-flight. Not thread-safe; one event loop only, which is all
    this process has."""

    def __init__(self, *, ttl_seconds: float = 900.0, max_entries: int = 20_000) -> None:
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._data: dict[tuple, _Entry] = {}
        self._inflight: dict[tuple, asyncio.Future] = {}
        self.stats = CacheStats()

    def _evict_if_needed(self) -> None:
        if len(self._data) <= self.max_entries:
            return
        # Drop expired first; if that is not enough, drop oldest-expiring. Insertion order
        # is close enough to LRU here because entries are written once and read many times.
        now = time.monotonic()
        for k in [k for k, v in self._data.items() if v.expires_at <= now]:
            del self._data[k]
            self.stats.evictions += 1
        while len(self._data) > self.max_entries:
            self._data.pop(next(iter(self._data)))
            self.stats.evictions += 1

    async def get_or_set(
        self,
        key: tuple,
        factory: Callable[[], Awaitable[Any]],
        *,
        cacheable: Callable[[Any], bool] | None = None,
    ) -> Any:
        """Return the cached value for `key`, or compute it exactly once.

        `cacheable` decides whether the computed result may be stored. Use it to keep
        transport failures out of the cache while still caching genuine empties.
        """
        now = time.monotonic()
        entry = self._data.get(key)
        if entry is not None and entry.expires_at > now:
            self.stats.hits += 1
            return entry.value
        if entry is not None:
            del self._data[key]

        inflight = self._inflight.get(key)
        if inflight is not None:
            self.stats.joins += 1
            return await asyncio.shield(inflight)

        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._inflight[key] = fut
        self.stats.misses += 1
        try:
            value = await factory()
        except BaseException as exc:
            # Everyone waiting on this key gets the same exception rather than hanging.
            if not fut.done():
                fut.set_exception(exc)
            self._inflight.pop(key, None)
            # Nobody may be awaiting the future; retrieve it so asyncio does not log
            # "exception was never retrieved" for a failure we are already raising.
            fut.exception()
            raise
        else:
            if cacheable is None or cacheable(value):
                self._data[key] = _Entry(value, time.monotonic() + self.ttl)
                self._evict_if_needed()
            else:
                self.stats.uncacheable += 1
            if not fut.done():
                fut.set_result(value)
            self._inflight.pop(key, None)
            return value

    def clear(self) -> None:
        self._data.clear()

    def as_dict(self) -> dict:
        return {**self.stats.as_dict(), "entries": len(self._data)}


# One cache for the process. Keys are namespaced by their first element, so a gene
# resolution and a disease resolution cannot collide on the same string.
CACHE = AsyncCache()
