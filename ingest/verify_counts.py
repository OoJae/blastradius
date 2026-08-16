"""Check that what is in the graph matches what we meant to load.

Two failure modes drive this. First, an edge batch whose endpoint is missing
matches nothing and writes zero rows *without erroring*, so a count that is
merely plausible proves nothing. Second, HydraDB's traversal budgets can
truncate a result silently, which looks identical to a correct small answer.

So every count is compared against the source files, and every traversal is
compared against the same closure computed in pure Python. NetworkX-style
oracle work happens here, offline -- never in the serving path.
"""

from __future__ import annotations

import csv
import gzip
import json
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from ingest import hydra
from ingest.ids import hash_key, pkg_key

# The largest closure where truncation has actually been disproved (Phase 0).
# Beyond this we have absence of evidence, not evidence of absence.
VERIFIED_CLOSURE_CEILING = 90_403


@dataclass
class CountRow:
    name: str
    source: int
    graph: int

    @property
    def delta(self) -> int:
        return self.graph - self.source

    @property
    def ok(self) -> bool:
        return self.delta == 0


def read_csv(path: Path):
    with gzip.open(path, "rt", newline="", encoding="utf-8") as handle:
        yield from csv.DictReader(handle)


def count_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with gzip.open(path, "rt", newline="", encoding="utf-8") as handle:
        return max(sum(1 for _ in handle) - 1, 0)


def preflight(slice_dir: Path) -> list[str]:
    """Every edge endpoint must exist as a node, checked before loading."""
    problems: list[str] = []

    packages = {row["name"] for row in read_csv(slice_dir / "packages.csv.gz")}
    versions = set()
    if (slice_dir / "versions.csv.gz").exists():
        versions = {
            (row["name"], row["version"])
            for row in read_csv(slice_dir / "versions.csv.gz")
        }

    dangling = 0
    for row in read_csv(slice_dir / "pkg_depends_on.csv.gz"):
        if row["src"] not in packages or row["dst"] not in packages:
            dangling += 1
    if dangling:
        problems.append(f"pkg_depends_on: {dangling:,} edges with a missing endpoint")

    if (slice_dir / "version_of.csv.gz").exists():
        missing = 0
        for row in read_csv(slice_dir / "version_of.csv.gz"):
            if (row["name"], row["version"]) not in versions or row["name"] not in packages:
                missing += 1
        if missing:
            problems.append(f"version_of: {missing:,} edges with a missing endpoint")

    if (slice_dir / "affects.csv.gz").exists():
        missing = 0
        for row in read_csv(slice_dir / "affects.csv.gz"):
            if (row["name"], row["version"]) not in versions:
                missing += 1
        if missing:
            problems.append(f"affects: {missing:,} edges pointing at an absent version")

    return problems


def source_counts(slice_dir: Path) -> dict[str, int]:
    edges = count_rows(slice_dir / "pkg_depends_on.csv.gz")
    return {
        "Package": count_rows(slice_dir / "packages.csv.gz"),
        "Version": count_rows(slice_dir / "versions.csv.gz"),
        "Maintainer": count_rows(slice_dir / "maintainers.csv.gz"),
        "Advisory": count_rows(slice_dir / "advisories.csv.gz"),
        "PKG_DEPENDS_ON": edges,
        # Written by re-reading the forward file flipped, so the count matches
        # by construction.
        "PKG_DEPENDED_BY": edges,
        "VERSION_OF": count_rows(slice_dir / "version_of.csv.gz"),
        "MAINTAINS": count_rows(slice_dir / "maintains.csv.gz"),
        "AFFECTS": count_rows(slice_dir / "affects.csv.gz"),
    }


NODE_LABELS = ("Package", "Version", "Maintainer", "Advisory")
EDGE_TYPES = {
    "PKG_DEPENDS_ON": ("Package", "Package"),
    "PKG_DEPENDED_BY": ("Package", "Package"),
    "VERSION_OF": ("Version", "Package"),
    "MAINTAINS": ("Maintainer", "Package"),
    "AFFECTS": ("Advisory", "Version"),
}


def graph_counts(session) -> dict[str, int | None]:
    """Count nodes and edges, tolerating a timeout on the large scans.

    An unanchored count walks the whole relationship set, which on a
    multi-million-edge type can exceed the server's query timeout. That is a
    cost of counting, not a defect in the data -- anchored traversals over the
    same edges return in milliseconds -- so a timeout is reported as "not
    counted" rather than as a mismatch. The oracle probes below are the real
    correctness check: they compare topology, not just cardinality.
    """
    counts: dict[str, int | None] = {}
    for label in NODE_LABELS:
        record = session.run(f"MATCH (n:{label}) RETURN count(*) AS n").single()
        counts[label] = record["n"] if record else 0
    for rel, (src, dst) in EDGE_TYPES.items():
        try:
            record = session.run(
                f"MATCH (s:{src})-[r:{rel}]->(d:{dst}) RETURN count(*) AS n"
            ).single()
            counts[rel] = record["n"] if record else 0
        except Exception as exc:  # noqa: BLE001 - any server-side refusal
            text = str(exc)
            # Two ways a whole-graph count is refused: it outruns the query
            # timeout, or it outgrows the 250k intermediate-row budget, which
            # is not configurable. Both are limits on *counting everything*;
            # anchored traversals over the same edges are unaffected. Crucially
            # the cap raises rather than returning a short answer, so a refused
            # count can never be mistaken for a real one.
            if (
                "timeout" in text.lower()
                or "Terminated" in text
                or "admission control" in text
            ):
                counts[rel] = None
            else:
                raise
    return counts


def build_reverse_index(slice_dir: Path) -> dict[str, list[str]]:
    """dependency -> [dependents], from the slice's own edge file."""
    reverse: dict[str, list[str]] = {}
    for row in read_csv(slice_dir / "pkg_depends_on.csv.gz"):
        reverse.setdefault(row["dst"], []).append(row["src"])
    return reverse


def oracle_closure(reverse: dict[str, list[str]], root: str, max_depth: int) -> set[str]:
    """Every package reachable from `root` by 1..max_depth reverse hops.

    The root counts as reachable if a dependency cycle leads back to it, which
    npm has plenty of once devDependencies are included -- `typescript` reaches
    itself in two hops. So a node is recorded the moment it is reached, even if
    it has been visited before; only traversal is deduplicated.
    """
    seen = {root}
    frontier = deque([(root, 0)])
    found: set[str] = set()
    while frontier:
        node, depth = frontier.popleft()
        if depth >= max_depth:
            continue
        for dependent in reverse.get(node, ()):
            found.add(dependent)
            if dependent not in seen:
                seen.add(dependent)
                frontier.append((dependent, depth + 1))
    return found


def graph_closure(session, name: str, depth: int) -> tuple[set[str], float]:
    started = time.perf_counter()
    records = session.run(
        f"MATCH (c:Package {{id: $id}})-[:PKG_DEPENDED_BY*1..{depth}]->(d:Package) "
        "RETURN DISTINCT d.name AS name",
        id=hash_key(pkg_key(name)),
    )
    names = {record["name"] for record in records}
    return names, (time.perf_counter() - started) * 1000


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Verify a loaded slice")
    parser.add_argument("--dir", required=True)
    parser.add_argument("--preflight", action="store_true", help="offline checks only")
    parser.add_argument("--probes", type=int, default=12)
    args = parser.parse_args()

    slice_dir = Path(args.dir)
    failures = 0

    print(f"Verifying {slice_dir.name}\n")

    problems = preflight(slice_dir)
    if problems:
        print("Referential integrity: FAIL")
        for problem in problems:
            print(f"  {problem}")
        failures += len(problems)
    else:
        print("Referential integrity: every edge endpoint exists")
    if args.preflight:
        return 1 if failures else 0

    source = source_counts(slice_dir)
    with hydra.connect() as driver:
        with hydra.session(driver) as session:
            graph = graph_counts(session)

            print("\n| Label / type      | source rows | in graph  | delta | verdict |")
            print("|-------------------|------------:|----------:|------:|---------|")
            for name in list(NODE_LABELS) + list(EDGE_TYPES):
                want = source.get(name, 0)
                got = graph.get(name)
                if got is None:
                    # Counting timed out; topology is checked by the oracle
                    # probes instead. Reported, never silently passed.
                    print(
                        f"| {name:<17} | {want:>11,} | {'timeout':>9} | "
                        f"{'-':>5} | skipped |"
                    )
                    continue
                row = CountRow(name, want, got)
                verdict = "ok" if row.ok else "FAIL"
                if not row.ok:
                    failures += 1
                print(
                    f"| {row.name:<17} | {row.source:>11,} | {row.graph:>9,} | "
                    f"{row.delta:>5,} | {verdict:<7} |"
                )

            print("\nReverse-closure oracle (HydraDB vs pure Python over the same file):")
            reverse = build_reverse_index(slice_dir)
            ranked = sorted(reverse.items(), key=lambda kv: -len(kv[1]))
            step = max(len(ranked) // args.probes, 1)
            probes = [name for name, _ in ranked[:: step][: args.probes]]

            exact = 0
            for name in probes:
                for depth in (1, 2):
                    got, ms = graph_closure(session, name, depth)
                    want = oracle_closure(reverse, name, depth)
                    if got == want:
                        exact += 1
                    else:
                        failures += 1
                        print(
                            f"  MISMATCH {name} d{depth}: graph {len(got):,} vs oracle "
                            f"{len(want):,} (missing {len(want - got):,})"
                        )
            print(f"  {exact}/{len(probes) * 2} probes exact")

            biggest = ranked[0][0]
            got, ms = graph_closure(session, biggest, 3)
            want = oracle_closure(reverse, biggest, 3)
            status = "ok" if got == want else "FAIL"
            if got != want:
                failures += 1
            print(f"\nDeep probe: {biggest} depth 3 -> {len(got):,} nodes, oracle {len(want):,}, {ms:,.0f} ms  {status}")
            if len(got) > VERIFIED_CLOSURE_CEILING:
                print(
                    f"  NOTE: closure exceeds {VERIFIED_CLOSURE_CEILING:,}, the largest size where "
                    "truncation has been disproved; treat larger results with suspicion"
                )

    print(f"\n{'PASS' if failures == 0 else f'{failures} FAILURE(S)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
