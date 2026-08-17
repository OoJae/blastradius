"""Reverse-dependency closures, cached because they are the expensive answer.

A radius is one anchored traversal, so it is fast for a single package. The
union across every compromised package is not: the demo slice takes about
thirteen seconds at depth 3, which is fine to compute once and unacceptable to
compute per lockfile upload.

The important field here is `ExposedSet.complete`. If any seed traversal was
refused, the union is a lower bound rather than an answer, and everything
downstream has to treat it that way -- a lockfile entry can only be called
clean when the closure that would have contained it actually finished.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from api.hydra import HydraClient, NotComputed
from api.trace import QueryRun, Trace
from ingest.ids import hash_key, pkg_key

TTL_SECONDS = 3600
MAX_ENTRIES = 256


@dataclass(frozen=True)
class Closure:
    seed: str
    depth: int
    names: frozenset[str]
    ms: float
    computed_at: float
    run: QueryRun


@dataclass(frozen=True)
class ExposedSet:
    depth: int
    names: frozenset[str]
    seeds_total: int
    seeds_refused: tuple[str, ...]
    ms: float
    computed_at: float

    @property
    def complete(self) -> bool:
        """Whether every seed's traversal finished.

        False makes this a lower bound: some package that belongs in the set
        may be missing, so absence from it proves nothing.
        """
        return not self.seeds_refused


class ClosureCache:
    def __init__(self, client: HydraClient) -> None:
        self._client = client
        self._radius: dict[tuple[str, int], Closure] = {}
        self._exposed: dict[int, ExposedSet] = {}
        self.warming = False

    def _fresh(self, computed_at: float) -> bool:
        return (time.time() - computed_at) < TTL_SECONDS

    async def radius(
        self, seed: str, depth: int, *, trace: Trace, fresh: bool = False
    ) -> Closure:
        key = (seed, depth)
        cached = self._radius.get(key)
        if cached is not None and not fresh and self._fresh(cached.computed_at):
            trace.replay(cached.run)
            return cached

        started = time.perf_counter()
        rows = await self._client.run_spec(
            "q1_radius_nodes",
            trace=trace,
            interpolate={"depth": depth},
            seed_id=hash_key(pkg_key(seed)),
            hint=f"depth {depth - 1} is cheaper" if depth > 1 else None,
        )
        closure = Closure(
            seed=seed,
            depth=depth,
            names=frozenset(row["name"] for row in rows),
            ms=(time.perf_counter() - started) * 1000,
            computed_at=time.time(),
            run=trace.steps[-1],
        )
        if len(self._radius) >= MAX_ENTRIES:
            self._radius.pop(next(iter(self._radius)))
        self._radius[key] = closure
        return closure

    def best_available(self, seed: str, below: int) -> Closure | None:
        """The deepest cached radius shallower than `below`.

        Used when a request times out: showing a shallower answer with its age
        attached is more useful than showing nothing.
        """
        for depth in range(below - 1, 0, -1):
            cached = self._radius.get((seed, depth))
            if cached is not None and self._fresh(cached.computed_at):
                return cached
        return None

    async def exposed_set(
        self, seeds: tuple[str, ...], depth: int, *, trace: Trace, fresh: bool = False
    ) -> ExposedSet:
        """The union closure over every compromised package."""
        cached = self._exposed.get(depth)
        if cached is not None and not fresh and self._fresh(cached.computed_at):
            return cached

        started = time.perf_counter()
        names: set[str] = set()
        refused: list[str] = []
        for seed in seeds:
            try:
                closure = await self.radius(seed, depth, trace=trace)
            except NotComputed:
                # One refusal makes the union a lower bound. Recorded by seed so
                # the report can say which, rather than just that it happened.
                refused.append(seed)
                continue
            names |= closure.names

        exposed = ExposedSet(
            depth=depth,
            names=frozenset(names),
            seeds_total=len(seeds),
            seeds_refused=tuple(refused),
            ms=(time.perf_counter() - started) * 1000,
            computed_at=time.time(),
        )
        self._exposed[depth] = exposed
        return exposed

    async def warm(self, seeds: tuple[str, ...], depth: int) -> None:
        """Precompute the incident's union in the background at boot."""
        if not seeds:
            return
        self.warming = True
        try:
            await self.exposed_set(seeds, depth, trace=Trace())
        finally:
            self.warming = False
