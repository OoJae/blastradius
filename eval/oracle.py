"""Offline closure maths, used to check what HydraDB returns.

Deliberately contains no database session, no Cypher and no neo4j import. If
the oracle went through HydraDB it would be comparing the system against
itself, and every exactness claim in the evaluation would be circular. A test
greps this file to keep it that way.
"""

from __future__ import annotations

import csv
import gzip
import json
from collections import deque
from pathlib import Path
from typing import Mapping, Sequence

from ingest.verify_counts import build_reverse_index, read_csv  # noqa: F401


def union_min_depth(
    reverse: Mapping[str, Sequence[str]],
    roots: Sequence[str],
    max_depth: int,
) -> dict[str, int]:
    """Package -> fewest reverse hops from any root, for hops 1..max_depth.

    Multi-source breadth-first search: seeding the visited set with every root
    at once gives the same answer as unioning per-root searches, at a fraction
    of the work. A node is recorded when reached rather than when first
    enqueued, so a root that a dependency cycle leads back to is counted --
    which npm does produce once devDependencies are in scope.
    """
    seen = set(roots)
    frontier: deque[tuple[str, int]] = deque((root, 0) for root in roots)
    depths: dict[str, int] = {}

    while frontier:
        node, depth = frontier.popleft()
        if depth >= max_depth:
            continue
        for dependent in reverse.get(node, ()):
            if dependent not in depths:
                depths[dependent] = depth + 1
            if dependent not in seen:
                seen.add(dependent)
                frontier.append((dependent, depth + 1))
    return depths


def closure_at(depths: Mapping[str, int], depth: int) -> frozenset[str]:
    """The 1..depth slice of a minimum-depth map."""
    return frozenset(name for name, found_at in depths.items() if found_at <= depth)


def maintainer_index(slice_dir: Path) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """(package -> maintainers, maintainer -> packages)."""
    by_package: dict[str, set[str]] = {}
    by_maintainer: dict[str, set[str]] = {}
    path = slice_dir / "maintains.csv.gz"
    if not path.exists():
        return by_package, by_maintainer
    for row in read_csv(path):
        by_package.setdefault(row["name"], set()).add(row["username"])
        by_maintainer.setdefault(row["username"], set()).add(row["name"])
    return by_package, by_maintainer


def similar_index(slice_dir: Path) -> dict[str, set[str]]:
    """package -> names within the typosquat distance cutoff."""
    index: dict[str, set[str]] = {}
    path = slice_dir / "similar_name.csv.gz"
    if not path.exists():
        return index
    for row in read_csv(path):
        index.setdefault(row["src"], set()).add(row["dst"])
    return index


def slice_roster(slice_dir: Path) -> frozenset[str]:
    with gzip.open(slice_dir / "packages.csv.gz", "rt", newline="", encoding="utf-8") as handle:
        return frozenset(row["name"] for row in csv.DictReader(handle))


def snapshot_roster(raw_packages_dir: Path) -> frozenset[str]:
    """Every package name in the deps.dev snapshot, for the not_in_snapshot code."""
    names: set[str] = set()
    for shard in sorted(raw_packages_dir.glob("*.csv.gz")):
        with gzip.open(shard, "rt", newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                names.add(row["name"])
    return frozenset(names)


def registry_unreachable(slice_dir: Path) -> frozenset[str]:
    """Packages the registry does not serve, recorded before the eval ran."""
    path = slice_dir / "registry_unreachable.json"
    if not path.exists():
        return frozenset()
    return frozenset(json.loads(path.read_text()).get("packages", []))
