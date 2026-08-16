"""Measure write throughput and traversal latency, and probe for silent truncation.

Produces the numbers behind the slice-tier decision. Nothing here is estimated:
every figure printed is measured against the running node, and failures are
reported rather than smoothed over.

Synthetic graph: a 7-layer DAG of 100k packages where each node in layer k has
three dependencies in layer k-1 (two uniform, one biased toward the layer's
first ten nodes so hubs and diamonds exist). Edges are written in both
directions, matching the real schema: PKG_DEPENDS_ON for dependent -> dependency
and PKG_DEPENDED_BY for the reverse projection the radius traverses.

Because the generator knows the edge list, it computes the exact reverse
closure of the biggest hub in Python. That is the oracle: if HydraDB returns
fewer nodes than the oracle at some depth, a query budget truncated the result
silently, which would otherwise look like a correct small answer.
"""

from __future__ import annotations

import json
import random
import statistics
import subprocess
import time
from collections import deque
from pathlib import Path

from neo4j.exceptions import Neo4jError

from ingest import hydra
from ingest.ids import hash_key, pkg_key, rel_id
from ingest.load import BatchWriter

SEED = 42
LAYER_SIZES = [100, 900, 4_000, 10_000, 20_000, 30_000, 35_000]
PARENTS_PER_NODE = 3
HUB_WIDTH = 10
MAX_DEPTH = 6
LATENCY_RUNS = 5

NODE_LABEL = "SpikePkg"
FORWARD = "SPIKE_DEPENDS_ON"
REVERSE = "SPIKE_DEPENDED_BY"


def build_graph() -> tuple[list[str], list[tuple[int, int]], list[int]]:
    """Names, edges as (dependent, dependency) index pairs, and layer offsets."""
    rng = random.Random(SEED)
    offsets, total = [], 0
    for size in LAYER_SIZES:
        offsets.append(total)
        total += size
    names = [f"spike/n{i}" for i in range(total)]

    edges: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for layer in range(1, len(LAYER_SIZES)):
        start, size = offsets[layer], LAYER_SIZES[layer]
        parent_start, parent_size = offsets[layer - 1], LAYER_SIZES[layer - 1]
        for i in range(start, start + size):
            picks = {
                parent_start + rng.randrange(parent_size),
                parent_start + rng.randrange(parent_size),
                parent_start + rng.randrange(min(HUB_WIDTH, parent_size)),
            }
            while len(picks) < PARENTS_PER_NODE:
                picks.add(parent_start + rng.randrange(parent_size))
            for parent in picks:
                edge = (i, parent)
                if edge not in seen:
                    seen.add(edge)
                    edges.append(edge)
    return names, edges, offsets


def oracle_closure(edges: list[tuple[int, int]], root: int, max_depth: int) -> dict[int, set[int]]:
    """Reverse closure of `root` by depth, computed in pure Python."""
    dependents: dict[int, list[int]] = {}
    for dependent, dependency in edges:
        dependents.setdefault(dependency, []).append(dependent)

    seen = {root}
    frontier = deque([(root, 0)])
    by_depth: dict[int, set[int]] = {d: set() for d in range(1, max_depth + 1)}
    while frontier:
        node, depth = frontier.popleft()
        if depth >= max_depth:
            continue
        for dependent in dependents.get(node, ()):
            if dependent not in seen:
                seen.add(dependent)
                by_depth[depth + 1].add(dependent)
                frontier.append((dependent, depth + 1))

    cumulative: dict[int, set[int]] = {}
    running: set[int] = set()
    for depth in range(1, max_depth + 1):
        running = running | by_depth[depth]
        cumulative[depth] = set(running)
    return cumulative


def time_query(session, cypher: str, params: dict, runs: int = LATENCY_RUNS):
    """Return (cold_ms, warm_p50_ms, row_count) for a query."""
    started = time.perf_counter()
    rows = len(list(session.run(cypher, **params)))
    cold_ms = (time.perf_counter() - started) * 1000

    warm = []
    for _ in range(runs):
        started = time.perf_counter()
        list(session.run(cypher, **params))
        warm.append((time.perf_counter() - started) * 1000)
    return cold_ms, statistics.median(warm), rows


def main() -> int:
    print("== generating synthetic graph")
    names, edges, offsets = build_graph()
    print(f"   {len(names):,} nodes across {len(LAYER_SIZES)} layers, {len(edges):,} edges")

    hub = offsets[0]  # a layer-0 node inside the hub-biased window
    oracle = oracle_closure(edges, hub, MAX_DEPTH)
    print(f"   oracle reverse closure of {names[hub]}: " + ", ".join(
        f"d{d}={len(oracle[d]):,}" for d in range(1, MAX_DEPTH + 1)
    ))

    node_rows = [{"vertex": hash_key(pkg_key(n)), "key": pkg_key(n)} for n in names]

    def edge_rows(rel_type: str, pairs) -> list[dict]:
        rows = []
        for a, b in pairs:
            src, dst = pkg_key(names[a]), pkg_key(names[b])
            rows.append(
                {
                    "src": hash_key(src),
                    "dst": hash_key(dst),
                    "rel": rel_id(rel_type, src, dst),
                    "via_versions": 1,
                }
            )
        return rows

    forward_rows = edge_rows(FORWARD, edges)
    reverse_rows = edge_rows(REVERSE, [(b, a) for a, b in edges])

    report: dict = {"nodes": len(node_rows), "edges": len(forward_rows) * 2}

    with hydra.connect() as driver:
        writer = BatchWriter(driver)

        print("\n== loading nodes (batches of 1024)")
        node_stats = writer.upsert_nodes(NODE_LABEL, ["key"], node_rows)
        print(
            f"   {node_stats.rows:,} nodes in {node_stats.seconds:.1f}s "
            f"= {node_stats.rows_per_second:,.0f} nodes/s "
            f"({node_stats.batches} batches, {node_stats.retries} retries)"
        )

        print("== loading edges (both directions)")
        fwd = writer.upsert_edges(FORWARD, NODE_LABEL, NODE_LABEL, ["via_versions"], forward_rows)
        rev = writer.upsert_edges(REVERSE, NODE_LABEL, NODE_LABEL, ["via_versions"], reverse_rows)
        edge_seconds = fwd.seconds + rev.seconds
        edge_count = fwd.rows + rev.rows
        edge_rate = edge_count / edge_seconds if edge_seconds else 0
        print(
            f"   {edge_count:,} edges in {edge_seconds:.1f}s = {edge_rate:,.0f} edges/s "
            f"({fwd.batches + rev.batches} batches, {fwd.retries + rev.retries} retries)"
        )

        report["nodes_per_second"] = node_stats.rows_per_second
        report["edges_per_second"] = edge_rate
        report["node_seconds"] = node_stats.seconds
        report["edge_seconds"] = edge_seconds

        with hydra.session(driver) as session:
            print("\n== reverse-closure latency and truncation check")
            print(f"   {'depth':>5}  {'cold ms':>9}  {'warm ms':>9}  {'returned':>9}  {'oracle':>9}  verdict")
            truncation = []
            for depth in range(1, MAX_DEPTH + 1):
                cypher = (
                    f"MATCH (c:{NODE_LABEL} {{id: $id}})-[:{REVERSE}*1..{depth}]->(d:{NODE_LABEL}) "
                    "RETURN DISTINCT d.key AS key"
                )
                try:
                    cold, warm, rows = time_query(session, cypher, {"id": hash_key(pkg_key(names[hub]))})
                except Neo4jError as exc:
                    print(f"   {depth:>5}  ERROR: {str(exc).splitlines()[0][:100]}")
                    truncation.append({"depth": depth, "error": str(exc).splitlines()[0]})
                    continue
                expected = len(oracle[depth])
                verdict = "exact" if rows == expected else f"SHORT by {expected - rows:,}"
                print(f"   {depth:>5}  {cold:>9.0f}  {warm:>9.0f}  {rows:>9,}  {expected:>9,}  {verdict}")
                truncation.append(
                    {"depth": depth, "cold_ms": cold, "warm_ms": warm, "returned": rows, "oracle": expected}
                )
            report["closure"] = truncation

            print("\n== count(*) over the same radius (paths, not distinct nodes)")
            for depth in (3, MAX_DEPTH):
                cypher = (
                    f"MATCH (c:{NODE_LABEL} {{id: $id}})-[:{REVERSE}*1..{depth}]->(d:{NODE_LABEL}) "
                    "RETURN count(*) AS n"
                )
                try:
                    cold, warm, _ = time_query(session, cypher, {"id": hash_key(pkg_key(names[hub]))}, runs=3)
                    value = session.run(cypher, id=hash_key(pkg_key(names[hub]))).single()["n"]
                    print(f"   depth {depth}: {value:,} path-hits, cold {cold:.0f}ms warm {warm:.0f}ms")
                except Neo4jError as exc:
                    print(f"   depth {depth}: ERROR {str(exc).splitlines()[0][:100]}")

            print("\n== path procedures")
            hub_key = pkg_key(names[hub])
            for label, cypher, params in [
                (
                    "SSpaths incoming, pathCount 200",
                    f"CALL algo.SSpaths({{sourceNode: $id, relTypes: ['{FORWARD}'], "
                    "relDirection: 'incoming', maxLen: 4, pathCount: 200, resultLimit: 1000}) "
                    "YIELD path RETURN path",
                    {"id": hash_key(hub_key)},
                ),
                (
                    "MSpaths 10 seeds incoming, pathCount 200",
                    "CALL algo.MSpaths({sourceLabel: '" + NODE_LABEL + "', sourceProperty: 'key', "
                    f"sourceValues: {hydra.cypher_string_list([pkg_key(names[offsets[0] + i]) for i in range(10)])}, "
                    f"relTypes: ['{FORWARD}'], relDirection: 'incoming', maxLen: 4, "
                    "pathCount: 200, resultLimit: 1000}) YIELD path RETURN path",
                    {},
                ),
            ]:
                try:
                    cold, warm, rows = time_query(session, cypher, params, runs=3)
                    print(f"   {label}: {rows} paths, cold {cold:.0f}ms warm {warm:.0f}ms")
                    report.setdefault("procedures", []).append(
                        {"label": label, "paths": rows, "cold_ms": cold, "warm_ms": warm}
                    )
                except Neo4jError as exc:
                    print(f"   {label}: ERROR {str(exc).splitlines()[0][:120]}")

            print("\n== prefix search (autocomplete)")
            cypher = f"MATCH (p:{NODE_LABEL}) WHERE p.key STARTS WITH $prefix RETURN p.key AS key LIMIT 10"
            cold, warm, rows = time_query(session, cypher, {"prefix": "pkg:npm/spike/n1"}, runs=3)
            print(f"   {rows} rows, cold {cold:.0f}ms warm {warm:.0f}ms")

    size = subprocess.run(
        ["du", "-sh", "hydradb-data/store"], capture_output=True, text=True
    ).stdout.strip()
    print(f"\n== object store on disk: {size}")
    report["store_size"] = size

    out = Path("docs/spike_results.json")
    out.write_text(json.dumps(report, indent=2))
    print(f"== wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
