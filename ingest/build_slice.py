"""Compile a loadable slice from the full npm dependency graph.

npm is far larger than we can load, so this picks a subgraph that keeps every
answer the product gives complete. The ordering matters:

* incident packages and campaign victims are the seeds -- they must be present
* their reverse dependents are the blast radius, expanded depth by depth while
  the budget allows
* a popularity spine gives the demo recognisable names
* one forward hop stops traversals dead-ending at the slice boundary

The package roster comes from the roster file, never from the edge list. Zero
dependency packages produce no manifest rows at all, so a roster derived from
edges would silently drop every leaf -- and their edges would then match no
endpoint and write nothing, without erroring.

Everything is sorted before truncation and ties break on name, so the same
inputs always produce the same slice.
"""

from __future__ import annotations

import csv
import gzip
import json
import sys
from array import array
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterator, Sequence

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "depsdev"
INCIDENT = ROOT / "data" / "incident"
SLICE_ROOT = ROOT / "data" / "slice"

# Dependency-kind bits as encoded by the extraction query.
KIND_RUNTIME, KIND_DEV, KIND_PEER, KIND_OPTIONAL = 1, 2, 4, 8
# The worm propagated through CI postinstall scripts, and CI installs dev
# dependencies, so they belong in the blast radius for this incident.
KINDS_ALL = KIND_RUNTIME | KIND_DEV | KIND_PEER | KIND_OPTIONAL


@dataclass(frozen=True)
class SliceBudget:
    tier: str = "T2"
    halo_max: int = 420_000
    halo_max_depth: int = 6
    spine_n: int = 120_000
    forward_max: int = 60_000
    kinds_mask: int = KINDS_ALL

    @classmethod
    def for_tier(cls, tier: str) -> "SliceBudget":
        if tier == "demo":
            # Sized by what loads reliably, not by what fits in memory. Every
            # edge is a durable write to object storage, so edge count -- not
            # package count -- is the binding constraint. The popularity spine
            # is the expensive part: those packages are the most depended upon,
            # so each one drags in a large share of the induced subgraph.
            return cls(tier="demo", halo_max=30_000, spine_n=12_000, forward_max=10_000)
        if tier == "hosted":
            # Sized to fit a 5 GB object-store volume. The store settles at
            # 1.7 GB for the demo tier but peaks well past 5 GB during
            # compaction, so the hosted slice keeps the whole incident core and
            # trims the popularity spine, which is what drives edge count.
            return cls(tier="hosted", halo_max=12_000, spine_n=3_000, forward_max=4_000)
        if tier == "T3":
            return cls(tier="T3", halo_max=120_000, spine_n=60_000, forward_max=20_000)
        if tier == "T1":
            return cls(tier="T1", halo_max=1_200_000, spine_n=400_000, forward_max=200_000)
        return cls()


def is_real_package(name: str) -> bool:
    """Whether a deps.dev name is an actual npm package.

    deps.dev encodes bundled dependencies as a path, so a package that ships a
    vendored copy of something appears as `parent>version>child`. Those are not
    npm packages -- the registry 404s on them, and the real parent is present
    separately with its own edges -- but they are 62% of the roster and 51% of
    the edge rows, so loading them would bury the real graph in fiction.

    `>` cannot occur in a legal npm package name, which makes this exact.
    """
    return name and ">" not in name


def _shards(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.csv.gz"))


def iter_csv(directory: Path) -> Iterator[dict[str, str]]:
    """Stream every row of a sharded csv.gz export in shard order."""
    for shard in _shards(directory):
        with gzip.open(shard, "rt", newline="", encoding="utf-8") as handle:
            yield from csv.DictReader(handle)


class PackageGraph:
    """Compressed sparse row graph over package names.

    Two counting passes over the edge file rather than a list of tuples: at
    ~100M edges the tuple list alone would not fit in memory.
    """

    def __init__(self) -> None:
        self.names: list[str] = []
        self.idx: dict[str, int] = {}
        self.fwd_off = array("l")
        self.fwd_dst = array("l")
        self.fwd_via = array("H")
        self.rev_off = array("l")
        self.rev_src = array("l")
        self.indeg = array("l")

    @classmethod
    def load(
        cls,
        roster_dir: Path,
        edges_dir: Path,
        *,
        kinds_mask: int = KINDS_ALL,
        verbose: bool = True,
    ) -> "PackageGraph":
        graph = cls()

        if verbose:
            print("  reading package roster...", flush=True)
        bundled = 0
        for row in iter_csv(roster_dir):
            name = row["name"]
            if not is_real_package(name):
                bundled += 1
                continue
            if name not in graph.idx:
                graph.idx[name] = len(graph.names)
                graph.names.append(name)
        n = len(graph.names)
        if verbose:
            print(
                f"  roster: {n:,} real packages ({bundled:,} bundled paths skipped)",
                flush=True,
            )
        graph.bundled_skipped = bundled

        fwd_deg = array("l", bytes(8 * n))
        rev_deg = array("l", bytes(8 * n))
        kept = skipped_kind = skipped_unknown = 0

        if verbose:
            print("  edge pass 1/2 (counting)...", flush=True)
        for row in iter_csv(edges_dir):
            if not int(row["kinds"]) & kinds_mask:
                skipped_kind += 1
                continue
            u = graph.idx.get(row["pkg"])
            v = graph.idx.get(row["dep"])
            if u is None or v is None:
                # A dependency on a package with no versions in the snapshot.
                skipped_unknown += 1
                continue
            fwd_deg[u] += 1
            rev_deg[v] += 1
            kept += 1

        graph.fwd_off = array("l", bytes(8 * (n + 1)))
        graph.rev_off = array("l", bytes(8 * (n + 1)))
        for i in range(n):
            graph.fwd_off[i + 1] = graph.fwd_off[i] + fwd_deg[i]
            graph.rev_off[i + 1] = graph.rev_off[i] + rev_deg[i]

        graph.fwd_dst = array("l", bytes(8 * kept))
        graph.fwd_via = array("H", bytes(2 * kept))
        graph.rev_src = array("l", bytes(8 * kept))
        fwd_cur = array("l", graph.fwd_off[:n])
        rev_cur = array("l", graph.rev_off[:n])

        if verbose:
            print("  edge pass 2/2 (filling)...", flush=True)
        for row in iter_csv(edges_dir):
            if not int(row["kinds"]) & kinds_mask:
                continue
            u = graph.idx.get(row["pkg"])
            v = graph.idx.get(row["dep"])
            if u is None or v is None:
                continue
            p = fwd_cur[u]
            graph.fwd_dst[p] = v
            graph.fwd_via[p] = min(int(row["via_versions"] or 1), 65535)
            fwd_cur[u] = p + 1
            q = rev_cur[v]
            graph.rev_src[q] = u
            rev_cur[v] = q + 1

        graph.indeg = array("l", (graph.rev_off[i + 1] - graph.rev_off[i] for i in range(n)))
        if verbose:
            print(
                f"  edges: {kept:,} kept, {skipped_kind:,} wrong-kind, "
                f"{skipped_unknown:,} endpoint not in roster",
                flush=True,
            )
        graph.stats = {
            "roster_bundled_paths_skipped": getattr(graph, "bundled_skipped", 0),
            "edges_kept": kept,
            "edges_skipped_kind": skipped_kind,
            "edges_skipped_unknown_endpoint": skipped_unknown,
        }
        return graph

    def dependents(self, i: int) -> Sequence[int]:
        return self.rev_src[self.rev_off[i] : self.rev_off[i + 1]]

    def dependencies(self, i: int) -> Sequence[int]:
        return self.fwd_dst[self.fwd_off[i] : self.fwd_off[i + 1]]


def load_seeds(graph: PackageGraph) -> tuple[list[int], list[str]]:
    """Incident packages plus campaign victims, as graph indices."""
    names: set[str] = set()
    payload = json.loads((INCIDENT / "compromised_versions.json").read_text())
    names.update(p["name"] for p in payload["packages"])

    campaign = INCIDENT / "campaign.json"
    if campaign.exists():
        for row in json.loads(campaign.read_text()).get("members", []):
            if row.get("ecosystem") == "npm":
                names.add(row["name"])

    present = sorted(n for n in names if n in graph.idx)
    missing = sorted(n for n in names if n not in graph.idx)
    return [graph.idx[n] for n in present], missing


def reverse_halo(
    graph: PackageGraph, seeds: list[int], budget: SliceBudget, *, verbose: bool = True
) -> tuple[dict[int, int], int, bool]:
    """Expand dependents depth by depth, accepting whole levels while they fit."""
    halo: dict[int, int] = {}
    seen = set(seeds)
    frontier: Sequence[int] = seeds
    depth_reached = 0
    truncated = False

    if verbose:
        print(f"  halo from {len(seeds):,} seeds (budget {budget.halo_max:,}):", flush=True)

    for depth in range(1, budget.halo_max_depth + 1):
        nxt = sorted({u for f in frontier for u in graph.dependents(f)} - seen)
        if not nxt:
            break

        room = budget.halo_max - len(halo)
        if len(nxt) > room:
            if verbose:
                print(
                    f"    depth {depth}: {len(nxt):,} new would exceed budget; "
                    f"taking top {room:,} by in-degree",
                    flush=True,
                )
            nxt = sorted(nxt, key=lambda i: (-graph.indeg[i], graph.names[i]))[:room]
            truncated = True

        for u in nxt:
            halo[u] = depth
            seen.add(u)
        depth_reached = depth
        if verbose:
            print(f"    depth {depth}: +{len(nxt):,} -> {len(halo):,} cumulative", flush=True)
        if truncated:
            break
        frontier = nxt

    return halo, depth_reached, truncated


CACHE = ROOT / "data" / "cache" / "graph"


def load_graph_cached(kinds_mask: int, *, verbose: bool = True) -> PackageGraph:
    """Load the CSR graph, reusing a cached build when one matches.

    Building it means parsing ~102M CSV rows out of 2.2 GB of gzip twice, which
    takes over half an hour. The result is deterministic for a given kinds
    mask, so it is worth keeping: tuning slice budgets otherwise pays that cost
    on every attempt.
    """
    import pickle

    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"graph-kinds{kinds_mask}.pickle"

    if path.exists():
        if verbose:
            print(f"  reusing cached graph ({path.stat().st_size / 1e9:.1f} GB)", flush=True)
        with path.open("rb") as handle:
            return pickle.load(handle)

    graph = PackageGraph.load(RAW / "packages", RAW / "pkg_edges", kinds_mask=kinds_mask)
    tmp = path.with_suffix(".tmp")
    with tmp.open("wb") as handle:
        pickle.dump(graph, handle, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(path)
    if verbose:
        print(f"  cached graph -> {path.name}", flush=True)
    return graph


def build(budget: SliceBudget, *, verbose: bool = True) -> Path:
    graph = load_graph_cached(budget.kinds_mask, verbose=verbose)

    seeds, missing_seeds = load_seeds(graph)
    if verbose:
        print(f"  seeds: {len(seeds):,} present, {len(missing_seeds)} absent from snapshot")

    halo, depth_reached, truncated = reverse_halo(graph, seeds, budget, verbose=verbose)

    core = set(seeds) | set(halo)
    candidates = (i for i in range(len(graph.names)) if i not in core)
    spine = sorted(candidates, key=lambda i: (-graph.indeg[i], graph.names[i]))[: budget.spine_n]
    core |= set(spine)
    if verbose:
        print(f"  spine: {len(spine):,} by in-degree")

    forward = sorted({v for u in core for v in graph.dependencies(u)} - core)
    if len(forward) > budget.forward_max:
        forward = sorted(forward, key=lambda i: (-graph.indeg[i], graph.names[i]))[
            : budget.forward_max
        ]
    if verbose:
        print(f"  forward hop: {len(forward):,}")

    members = sorted(core | set(forward))
    in_slice = bytearray(len(graph.names))
    for i in members:
        in_slice[i] = 1

    out = SLICE_ROOT / f"{budget.tier}-20260810"
    out.mkdir(parents=True, exist_ok=True)

    with gzip.open(out / "packages.csv.gz", "wt", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["name", "is_popular", "in_degree"])
        spine_set = set(spine)
        for i in members:
            writer.writerow([graph.names[i], int(i in spine_set), graph.indeg[i]])

    edge_count = 0
    with gzip.open(out / "pkg_depends_on.csv.gz", "wt", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["src", "dst", "via_versions"])
        for u in members:
            lo, hi = graph.fwd_off[u], graph.fwd_off[u + 1]
            for p in range(lo, hi):
                v = graph.fwd_dst[p]
                if in_slice[v]:
                    writer.writerow([graph.names[u], graph.names[v], graph.fwd_via[p]])
                    edge_count += 1

    manifest = {
        "tier": budget.tier,
        "snapshot": "2026-08-10",
        "budget": asdict(budget),
        "packages": len(members),
        "pkg_depends_on": edge_count,
        "seeds_present": len(seeds),
        "seeds_missing": missing_seeds,
        "halo_size": len(halo),
        "halo_depth_reached": depth_reached,
        "halo_truncated": truncated,
        "spine": len(spine),
        "forward_hop": len(forward),
        "graph_stats": getattr(graph, "stats", {}),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    if verbose:
        print(f"\n  slice: {len(members):,} packages, {edge_count:,} edges -> {out}")
    return out


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Build a loadable slice")
    parser.add_argument("--tier", default="T2", choices=["T1", "T2", "T3", "demo", "hosted"])
    args = parser.parse_args()

    budget = SliceBudget.for_tier(args.tier)
    print(f"building slice {budget.tier}")
    out = build(budget)
    (SLICE_ROOT / "CURRENT").write_text(str(out) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
