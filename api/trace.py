"""The record of what the database actually did for a request.

Every response carries this, because the interesting claim BlastRadius makes is
that a graph answered the question -- and a claim like that is only worth
anything if the statement and its milliseconds travel with the answer.

Each step also carries whether it was served from cache. Two endpoints are
cached deliberately, and a reader has to be able to tell which numbers were
computed just now and which were computed earlier.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class QueryRun:
    name: str
    cypher: str
    params: dict[str, Any]
    ms: float
    rows: int
    cached: bool = False
    computed_at: float | None = None
    error: str | None = None

    def as_step(self) -> dict[str, Any]:
        step: dict[str, Any] = {
            "name": self.name,
            "cypher": self.cypher,
            "params": _safe_params(self.params),
            "ms": round(self.ms, 1),
            "rows": self.rows,
            "cached": self.cached,
        }
        if self.computed_at is not None:
            step["computed_at"] = round(self.computed_at, 3)
        if self.error is not None:
            step["error"] = self.error
        return step


def _safe_params(params: dict[str, Any]) -> dict[str, Any]:
    """Parameters, with anything unprintable summarised rather than dumped."""
    out: dict[str, Any] = {}
    for key, value in params.items():
        if isinstance(value, (int, float, bool, str)) or value is None:
            out[key] = value
        elif isinstance(value, (list, tuple, set)):
            out[key] = f"<{len(value)} values>"
        else:
            out[key] = f"<{type(value).__name__}>"
    return out


@dataclass
class Trace:
    steps: list[QueryRun] = field(default_factory=list)
    started: float = field(default_factory=time.perf_counter)

    def add(self, run: QueryRun) -> QueryRun:
        self.steps.append(run)
        return run

    def replay(self, run: QueryRun) -> QueryRun:
        """Record a step that was answered from cache rather than re-run."""
        return self.add(
            QueryRun(
                name=run.name,
                cypher=run.cypher,
                params=run.params,
                ms=run.ms,
                rows=run.rows,
                cached=True,
                computed_at=run.computed_at,
            )
        )

    @property
    def live_ms(self) -> float:
        """Database time spent on this request, excluding cached answers."""
        return sum(step.ms for step in self.steps if not step.cached)

    def envelope(self, primary: str | None = None) -> dict[str, Any]:
        """The `hydra` block attached to every response, success or failure."""
        chosen: QueryRun | None = None
        if primary is not None:
            chosen = next((s for s in self.steps if s.name == primary), None)
        if chosen is None:
            # Default to the step that did the most work, which is almost always
            # the one worth showing.
            live = [s for s in self.steps if not s.cached]
            chosen = max(live or self.steps, key=lambda s: s.ms, default=None)

        return {
            "cypher": chosen.cypher if chosen else "",
            "ms": round(self.live_ms, 1),
            "queries": len(self.steps),
            "live": sum(1 for s in self.steps if not s.cached),
            "cached": sum(1 for s in self.steps if s.cached),
            "steps": [step.as_step() for step in self.steps],
        }
