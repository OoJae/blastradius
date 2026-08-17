"""The serving-path client: one driver, a session per statement, bounded work.

The `neo4j` driver here is synchronous and the server is async, which has to be
resolved deliberately rather than by hoping. The driver object is thread-safe;
a `Session` is not, and sharing one across requests corrupts results
non-deterministically -- the worst kind of bug to meet during a live demo. So
every statement runs in a worker thread that opens its own session, uses it,
and closes it. A session is never stored, reused, or passed between threads.

Work is bounded two ways. A semaphore caps how many statements can be in flight,
so a burst of deep traversals cannot stampede a single-node database. And a
deadline well inside the server's own ceiling means a query that will never
finish surfaces as an error in seconds rather than as a spinner for two minutes.

Nothing here returns a bare failure: an endpoint that cannot answer says so, in
the same shape as an answer, carrying the statement that failed.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any, Literal

import anyio
from neo4j import Driver, GraphDatabase
from starlette.concurrency import run_in_threadpool

from api.queries import render
from api.trace import QueryRun, Trace
from ingest import hydra as ingest_hydra

# Enough concurrency to keep the UI responsive, few enough that a handful of
# depth-3 traversals cannot saturate the database.
MAX_INFLIGHT = int(os.environ.get("HYDRA_MAX_INFLIGHT", "4"))

# Comfortably inside GRAPH_MAX_QUERY_RUNTIME_MS (120 s), so we time out first
# and can say something useful about it.
REQUEST_DEADLINE_S = float(os.environ.get("HYDRA_DEADLINE_S", "25"))

Kind = Literal["admission_control", "timeout", "unavailable", "rejected", "deadline"]


@dataclass
class NotComputed(Exception):
    """The database could not answer, and we will not pretend otherwise."""

    kind: Kind
    message: str
    cypher: str = ""
    ms: float = 0.0
    hint: str | None = None

    @property
    def retryable(self) -> bool:
        return self.kind in ("timeout", "unavailable", "deadline")

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "message": self.message,
            "hint": self.hint,
            "retryable": self.retryable,
        }


def classify(exc: Exception, *, cypher: str = "", ms: float = 0.0, hint: str | None = None) -> NotComputed:
    """Map a driver failure onto something an endpoint can explain.

    The markers are the exact strings measured against this server and recorded
    in docs/VERIFICATION.md, not guesses at what a Neo4j-compatible database
    might say.
    """
    text = str(exc)
    first = text.splitlines()[0][:300] if text else exc.__class__.__name__

    if isinstance(exc, asyncio.TimeoutError) or isinstance(exc, TimeoutError):
        return NotComputed("deadline", f"exceeded the {REQUEST_DEADLINE_S:.0f}s API deadline", cypher, ms, hint)

    lowered = text.lower()
    if "admission control" in lowered or "memorypool" in lowered:
        return NotComputed("admission_control", first, cypher, ms, hint)
    if "timeout" in lowered or "terminated" in text:
        return NotComputed("timeout", first, cypher, ms, hint)
    if any(
        marker in text
        for marker in (
            "is not supported yet",
            "InvalidSyntax",
            "Invalid input",
            "requires a fixed source id",
            "only supported as an UNWIND input",
            "not executable",
        )
    ):
        # Our bug rather than the database's; parse-check exists to make this
        # unreachable in a released build.
        return NotComputed("rejected", first, cypher, ms, hint)
    if "defunct" in lowered or "unavailable" in lowered or "expired" in lowered:
        return NotComputed("unavailable", first, cypher, ms, hint)
    return NotComputed("unavailable", first, cypher, ms, hint)


class HydraClient:
    def __init__(self, driver: Driver) -> None:
        self._driver = driver
        self._limit = anyio.Semaphore(MAX_INFLIGHT)
        self.counters = {"live": 0, "failed": 0}

    @classmethod
    def open(cls) -> "HydraClient":
        driver = GraphDatabase.driver(
            ingest_hydra.bolt_uri(),
            auth=("neo4j", ingest_hydra.auth_token()),
            max_connection_pool_size=MAX_INFLIGHT + 2,
            connection_acquisition_timeout=10,
        )
        driver.verify_connectivity()
        return cls(driver)

    def close(self) -> None:
        self._driver.close()

    def run_sync(self, name: str, cypher: str, **params: Any) -> tuple[list[dict], QueryRun]:
        """Execute one statement in the calling thread, with its own session.

        Timing spans consumption as well as execution: the driver streams
        lazily, so timing only the call would report a number that has nothing
        to do with how long the answer took to arrive.
        """
        started = time.perf_counter()
        with self._driver.session(database=ingest_hydra.database()) as session:
            result = session.run(cypher, **params)
            rows = [dict(record) for record in result]
        ms = (time.perf_counter() - started) * 1000
        return rows, QueryRun(name, cypher, params, ms, len(rows), computed_at=time.time())

    async def run(
        self,
        name: str,
        cypher: str,
        *,
        trace: Trace,
        hint: str | None = None,
        **params: Any,
    ) -> list[dict]:
        started = time.perf_counter()
        try:
            async with self._limit:
                with anyio.fail_after(REQUEST_DEADLINE_S):
                    rows, run = await run_in_threadpool(self.run_sync, name, cypher, **params)
        except Exception as exc:  # noqa: BLE001 - mapped, never swallowed
            ms = (time.perf_counter() - started) * 1000
            self.counters["failed"] += 1
            trace.add(QueryRun(name, cypher, params, ms, 0, error=str(exc).splitlines()[0][:200]))
            raise classify(exc, cypher=cypher, ms=ms, hint=hint) from exc

        self.counters["live"] += 1
        trace.add(run)
        return rows

    async def run_spec(
        self,
        spec: str,
        *,
        trace: Trace,
        interpolate: dict[str, Any] | None = None,
        hint: str | None = None,
        **params: Any,
    ) -> list[dict]:
        """Run a registered query, rendered through the interpolation guard."""
        return await self.run(
            spec, render(spec, **(interpolate or {})), trace=trace, hint=hint, **params
        )
