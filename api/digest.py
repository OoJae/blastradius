"""What the service learns from the graph once, at startup.

Two answers cannot be computed per request. A prefix search costs about five
seconds because this Cypher subset has no index DDL, and a package count costs
two and a half. Both are read out of HydraDB at boot and answered from memory
afterwards, which makes them caches of graph results rather than a second query
engine: the lookup is a binary search over names HydraDB gave us, and it cannot
return a name HydraDB did not.

The same pass discovers *which* advisory this graph holds. The fixture graph a
stranger gets from `just ingest-demo` contains a different advisory from the
demo slice, so nothing here may assume the real incident is present -- the
service reports what it found and says plainly when the incident is absent.
"""

from __future__ import annotations

import bisect
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from api.hydra import HydraClient, NotComputed
from api.queries import COUNTABLE_LABELS
from api.trace import QueryRun, Trace

# The advisory this product was built to demonstrate. Its absence is a
# supported state, not an error.
INCIDENT_ADVISORY = "GHSA-g7cv-rxg3-hmpx"


@dataclass(frozen=True)
class Artifact:
    name: str
    version: str
    published_at: int
    live_from: int
    live_until: int


@dataclass
class GraphDigest:
    names: list[str] = field(default_factory=list)
    popular: set[str] = field(default_factory=set)
    versions: set[tuple[str, str]] = field(default_factory=set)

    advisory_key: str | None = None
    artifacts: dict[tuple[str, str], Artifact] = field(default_factory=dict)
    incident_packages: tuple[str, ...] = ()

    counts: dict[str, int | None] = field(default_factory=dict)
    count_errors: dict[str, str] = field(default_factory=dict)
    boot: list[QueryRun] = field(default_factory=list)
    built_at: float = 0.0

    # Sorted search keys, and the name each maps to. A scoped package appears
    # twice so that typing the bare name finds it.
    _keys: list[str] = field(default_factory=list)
    _targets: list[str] = field(default_factory=list)

    @property
    def has_incident(self) -> bool:
        return self.advisory_key is not None and INCIDENT_ADVISORY in self.advisory_key

    @property
    def window(self) -> tuple[int, int] | None:
        if not self.artifacts:
            return None
        return (
            min(a.live_from for a in self.artifacts.values()),
            max(a.live_until for a in self.artifacts.values()),
        )

    def has_package(self, name: str) -> bool:
        index = bisect.bisect_left(self.names, name)
        return index < len(self.names) and self.names[index] == name

    def has_version(self, name: str, version: str) -> bool:
        return (name, version) in self.versions

    def artifact(self, name: str, version: str) -> Artifact | None:
        return self.artifacts.get((name, version))

    def suggest(self, prefix: str, limit: int = 10) -> list[dict[str, Any]]:
        """Prefix lookup by binary search, ranked so useful names come first."""
        if not prefix:
            return []
        lowered = prefix.lower()
        start = bisect.bisect_left(self._keys, lowered)

        seen: set[str] = set()
        hits: list[str] = []
        for position in range(start, len(self._keys)):
            if not self._keys[position].startswith(lowered):
                break
            name = self._targets[position]
            if name not in seen:
                seen.add(name)
                hits.append(name)
            if len(hits) > limit * 8:
                break

        hits.sort(key=lambda n: (n not in self.popular, len(n), n))
        return [{"name": n, "is_popular": n in self.popular} for n in hits[:limit]]

    def memory_bytes(self) -> int:
        return (
            sum(sys.getsizeof(n) for n in self.names)
            + sum(sys.getsizeof(k) for k in self._keys)
            + sys.getsizeof(self.names)
            + sys.getsizeof(self._keys)
            + sys.getsizeof(self._targets)
        )

    @classmethod
    async def build(cls, client: HydraClient) -> "GraphDigest":
        digest = cls()
        trace = Trace()

        rows = await client.run_spec("q9_package_roster", trace=trace)
        digest.names = sorted(row["name"] for row in rows)
        digest.popular = {row["name"] for row in rows if row.get("is_popular")}

        keyed: list[tuple[str, str]] = []
        for name in digest.names:
            keyed.append((name.lower(), name))
            if name.startswith("@") and "/" in name:
                # So typing `react-router` finds `@tanstack/react-router`.
                keyed.append((name.split("/", 1)[1].lower(), name))
        keyed.sort()
        digest._keys = [key for key, _ in keyed]
        digest._targets = [target for _, target in keyed]

        version_rows = await client.run_spec("q10_version_roster", trace=trace)
        digest.versions = {(row["name"], row["version"]) for row in version_rows}

        for label in sorted(COUNTABLE_LABELS):
            try:
                result = await client.run_spec(
                    "q8_label_count", trace=trace, interpolate={"label": label}
                )
                digest.counts[label] = result[0]["n"] if result else 0
            except NotComputed as exc:
                # Recorded rather than hidden: a count we could not take is a
                # different thing from a count of zero.
                digest.counts[label] = None
                digest.count_errors[label] = exc.message

        await digest._discover_advisory(client, trace)

        digest.boot = list(trace.steps)
        digest.built_at = time.time()
        return digest

    async def _discover_advisory(self, client: HydraClient, trace: Trace) -> None:
        """Find which advisory this graph holds, rather than assuming one."""
        from ingest.ids import adv_key, hash_key

        found = await client.run("advisory_scan", "MATCH (a:Advisory) RETURN a.key AS key", trace=trace)
        keys = [row["key"] for row in found if row.get("key")]
        if not keys:
            return

        # Prefer the incident this product demonstrates; otherwise take
        # whatever the graph does hold, so a fixture graph still works.
        self.advisory_key = next(
            (k for k in keys if INCIDENT_ADVISORY in k), sorted(keys)[0]
        )

        artifacts = await client.run_spec(
            "q3_advisory_manifest",
            trace=trace,
            advisory_id=hash_key(self.advisory_key),
        )
        for row in artifacts:
            artifact = Artifact(
                name=row["name"],
                version=row["version"],
                published_at=row["published_at"],
                live_from=row["live_from"],
                live_until=row["live_until"],
            )
            self.artifacts[(artifact.name, artifact.version)] = artifact
        self.incident_packages = tuple(sorted({a.name for a in self.artifacts.values()}))
