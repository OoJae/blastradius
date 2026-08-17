"""How the incident unfolded, and when exposure actually happened.

The obvious assumption about this attack is that the blast radius grew across
the six-minute publish window. It did not. The 42 packages each received a
malicious version within the first four seconds, so the package-level radius was
complete almost immediately and then sat unchanged for the remaining five and a
half minutes, until a second automated burst published a second version of each
package.

That is worth showing rather than smoothing over: two four-second bursts is a
sharper description of an automated attack than a gradual crawl, and it is what
the timestamps say.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from api.digest import Artifact

# Publishes closer together than this belong to the same burst. The real gap
# between the two waves here is ~5.5 minutes, so the boundary is not delicate.
WAVE_GAP_SECONDS = 60


@dataclass(frozen=True)
class Wave:
    index: int
    started: int
    ended: int
    versions: int
    packages: int

    @property
    def seconds(self) -> int:
        return self.ended - self.started


def group_waves(artifacts: Sequence[Artifact]) -> list[Wave]:
    """Cluster publishes into bursts separated by a quiet gap."""
    if not artifacts:
        return []

    ordered = sorted(artifacts, key=lambda a: a.published_at)
    groups: list[list[Artifact]] = [[ordered[0]]]
    for artifact in ordered[1:]:
        if artifact.published_at - groups[-1][-1].published_at > WAVE_GAP_SECONDS:
            groups.append([artifact])
        else:
            groups[-1].append(artifact)

    return [
        Wave(
            index=index,
            started=group[0].published_at,
            ended=group[-1].published_at,
            versions=len(group),
            packages=len({a.name for a in group}),
        )
        for index, group in enumerate(groups, start=1)
    ]


def exposure_curve(
    artifacts: Sequence[Artifact], closures: dict[str, frozenset[str]]
) -> list[dict]:
    """Cumulative exposure at each moment a package first turned malicious.

    Keyed on the first malicious publish per package, because a package's
    dependents are exposed from the moment any malicious version of it is
    available -- a second version later adds nothing to the reachable set.
    """
    first_publish: dict[str, int] = {}
    for artifact in artifacts:
        current = first_publish.get(artifact.name)
        if current is None or artifact.published_at < current:
            first_publish[artifact.name] = artifact.published_at

    if not first_publish:
        return []

    start = min(first_publish.values())
    by_time: dict[int, list[str]] = {}
    for name, when in first_publish.items():
        by_time.setdefault(when, []).append(name)

    reached: set[str] = set()
    seeds_live = 0
    curve: list[dict] = []
    for when in sorted(by_time):
        for name in by_time[when]:
            seeds_live += 1
            reached |= closures.get(name, frozenset())
        curve.append(
            {
                "t": when,
                "offset": when - start,
                "packages_compromised": seeds_live,
                "exposed": len(reached),
            }
        )
    return curve


def saturation(curve: Sequence[dict]) -> dict | None:
    """When the exposed set stopped growing, and how far into the window."""
    if not curve:
        return None
    peak = max(point["exposed"] for point in curve)
    first_at_peak = next(point for point in curve if point["exposed"] == peak)
    return {
        "exposed": peak,
        "t": first_at_peak["t"],
        "offset_seconds": first_at_peak["offset"],
        "note": (
            "the exposed set stops growing here; every later publish adds "
            "artifacts but no newly reachable packages"
        ),
    }
