"""Small thread-safe caches for deterministic portfolio analytics."""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from threading import Condition, RLock
from typing import TypeVar, cast

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


@dataclass(frozen=True)
class CacheStats:
    hits: int
    misses: int
    evictions: int
    size: int
    pending: int


@dataclass
class _Pending[V]:
    done: bool = False
    value: V | None = None
    error: BaseException | None = None


class SingleFlightLru[K, V]:
    """Bounded LRU where concurrent misses for one key share one builder."""

    def __init__(self, max_entries: int):
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self._max_entries = max_entries
        self._values: OrderedDict[K, V] = OrderedDict()
        self._pending: dict[K, _Pending[V]] = {}
        self._condition = Condition(RLock())
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def get_or_compute(self, key: K, builder: Callable[[], V]) -> V:
        with self._condition:
            cached = self._values.get(key)
            if cached is not None or key in self._values:
                self._values.move_to_end(key)
                self._hits += 1
                return cast(V, cached)

            pending = self._pending.get(key)
            if pending is not None:
                self._hits += 1
                while not pending.done:
                    self._condition.wait()
                if pending.error is not None:
                    raise pending.error
                return cast(V, pending.value)

            pending = _Pending[V]()
            self._pending[key] = pending
            self._misses += 1

        try:
            value = builder()
        except BaseException as exc:
            with self._condition:
                pending.error = exc
                pending.done = True
                self._pending.pop(key, None)
                self._condition.notify_all()
            raise

        with self._condition:
            self._values[key] = value
            self._values.move_to_end(key)
            while len(self._values) > self._max_entries:
                self._values.popitem(last=False)
                self._evictions += 1
            pending.value = value
            pending.done = True
            self._pending.pop(key, None)
            self._condition.notify_all()
        return value

    def clear(self) -> None:
        with self._condition:
            self._values.clear()
            self._hits = 0
            self._misses = 0
            self._evictions = 0

    def stats(self) -> CacheStats:
        with self._condition:
            return CacheStats(
                hits=self._hits,
                misses=self._misses,
                evictions=self._evictions,
                size=len(self._values),
                pending=len(self._pending),
            )


def fingerprint(payload: object) -> str:
    """Stable process-independent digest for JSON-compatible analytics inputs."""
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()
