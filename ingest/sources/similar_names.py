"""Typosquat edges: package names within a couple of edits of a real one.

Typosquatting is the other way a supply chain gets attacked, and unlike the
dependency graph it is a property of names alone. The product only ever asks
for neighbours of a package someone searched for, which is always an incident
package or a well-known one -- so this compares a few thousand query names
against the slice rather than every pair, which would be ~1.4 billion
comparisons for no benefit.

Two filters do most of the work:

* A Damerau-Levenshtein distance of 2 cannot change a name's length by more
  than 2, so only names within that band need comparing at all.
* Packages sharing a scope are siblings, not squats. `@uipath/foo-bar` and
  `@uipath/foo-baz` are one edit apart and obviously unrelated to typosquatting,
  and since most campaign victims are scoped this filter is the difference
  between a useful edge set and noise.
"""

from __future__ import annotations

import csv
import gzip
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np
from rapidfuzz.distance import DamerauLevenshtein
from rapidfuzz.process import cdist

MAX_DISTANCE = 2
# Short names have enormous neighbourhoods -- `react` has thousands of names
# within two edits -- so without a cap the similarity edges would outnumber the
# dependency edges and drown the graph they are meant to annotate.
MAX_NEIGHBOURS = 25
QUERY_BLOCK = 256


@dataclass(frozen=True)
class SimilarPair:
    """One canonical pair, `a < b`, so a pair found from both ends dedupes."""

    a: str
    b: str
    distance: int


def scope_of(name: str) -> str | None:
    if name.startswith("@") and "/" in name:
        return name.split("/", 1)[0]
    return None


def same_scope(a: str, b: str) -> bool:
    scope = scope_of(a)
    return scope is not None and scope == scope_of(b)


def length_index(corpus: Sequence[str]) -> dict[int, list[int]]:
    index: dict[int, list[int]] = {}
    for position, name in enumerate(corpus):
        index.setdefault(len(name), []).append(position)
    return index


def similar_pairs(
    queries: Sequence[str],
    corpus: Sequence[str],
    *,
    rank: Mapping[str, int] | None = None,
    max_distance: int = MAX_DISTANCE,
    max_neighbours: int = MAX_NEIGHBOURS,
    block: int = QUERY_BLOCK,
) -> tuple[list[SimilarPair], dict]:
    """Find near-identical names, returning canonical pairs and what was capped."""
    rank = rank or {}
    by_length = length_index(corpus)
    corpus_array = np.array(corpus, dtype=object)

    canonical: dict[tuple[str, str], int] = {}
    truncated: Counter[str] = Counter()
    compared = 0

    for start in range(0, len(queries), block):
        chunk = queries[start : start + block]
        low = max(min(len(q) for q in chunk) - max_distance, 1)
        high = max(len(q) for q in chunk) + max_distance

        positions: list[int] = []
        for length in range(low, high + 1):
            positions.extend(by_length.get(length, ()))
        if not positions:
            continue

        candidates = corpus_array[np.array(positions, dtype=np.int64)]
        compared += len(chunk) * len(candidates)

        # uint8 keeps a 256 x N block in tens of megabytes; anything beyond the
        # cutoff comes back as 3 and is discarded.
        matrix = cdist(
            chunk,
            candidates,
            scorer=DamerauLevenshtein.distance,
            score_cutoff=max_distance,
            dtype=np.uint8,
            workers=-1,
        )

        for row, query in enumerate(chunk):
            hits = [
                (str(candidates[column]), int(matrix[row, column]))
                for column in np.flatnonzero(matrix[row] <= max_distance)
            ]
            hits = [
                (name, distance)
                for name, distance in hits
                if name != query and distance >= 1 and not same_scope(query, name)
            ]
            # Closest first, then most-depended-upon: if the list has to be cut,
            # keep the squats worth warning about.
            hits.sort(key=lambda hit: (hit[1], -rank.get(hit[0], 0), hit[0]))
            if len(hits) > max_neighbours:
                truncated[query] = len(hits) - max_neighbours
            for name, distance in hits[:max_neighbours]:
                key = (query, name) if query < name else (name, query)
                canonical[key] = min(canonical.get(key, 99), distance)

    pairs = [SimilarPair(a, b, distance) for (a, b), distance in sorted(canonical.items())]
    stats = {
        "queries": len(queries),
        "corpus": len(corpus),
        "comparisons": compared,
        "canonical_pairs": len(pairs),
        "queries_truncated": len(truncated),
        "neighbours_dropped": int(sum(truncated.values())),
        "max_neighbours": max_neighbours,
        "max_distance": max_distance,
    }
    return pairs, stats


def build(slice_dir: Path, *, popular: int = 2000, verbose: bool = True) -> dict:
    """Write similar_name.csv.gz for a built slice."""
    incident_dir = Path(__file__).resolve().parents[2] / "data" / "incident"

    rows: list[tuple[str, int]] = []
    with gzip.open(slice_dir / "packages.csv.gz", "rt", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            rows.append((row["name"], int(row["in_degree"])))

    corpus = [name for name, _ in rows]
    in_slice = set(corpus)
    rank = dict(rows)

    queries: set[str] = set()
    compromised = json.loads((incident_dir / "compromised_versions.json").read_text())
    queries.update(p["name"] for p in compromised["packages"])
    campaign_path = incident_dir / "campaign.json"
    if campaign_path.exists():
        queries.update(
            m["name"]
            for m in json.loads(campaign_path.read_text())["members"]
            if m["ecosystem"] == "npm"
        )
    queries.update(name for name, _ in sorted(rows, key=lambda r: -r[1])[:popular])
    # Only ask about names the graph actually holds; a query for an absent
    # package would produce edges with no endpoint.
    query_list = sorted(q for q in queries if q in in_slice)

    if verbose:
        print(f"  {len(query_list):,} query names against {len(corpus):,} corpus names", flush=True)

    pairs, stats = similar_pairs(query_list, corpus, rank=rank)

    # Both directions, so a lookup from either end finds the other.
    out = slice_dir / "similar_name.csv.gz"
    written = 0
    with gzip.open(out, "wt", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["src", "dst", "distance"])
        for pair in pairs:
            writer.writerow([pair.a, pair.b, pair.distance])
            writer.writerow([pair.b, pair.a, pair.distance])
            written += 2

    stats["edges_written"] = written
    (slice_dir / "similar_name_stats.json").write_text(
        json.dumps(stats, indent=2, sort_keys=True) + "\n"
    )
    if verbose:
        for key, value in stats.items():
            print(f"  {key:22s} {value:,}")
    return stats


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Build typosquat edges for a slice")
    parser.add_argument("--dir", required=True)
    parser.add_argument("--popular", type=int, default=2000)
    args = parser.parse_args()
    build(Path(args.dir), popular=args.popular)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
