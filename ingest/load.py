"""Batched writer for HydraDB, plus the fixture loader.

Every statement shape here was verified against a running node in Phase 0 (see
docs/VERIFICATION.md). The constraints that shape this code:

* an UNWIND batch may carry at most 1024 rows
* a node upsert merges on id alone; the label and all properties are applied
  with a following SET, and every SET value must read a field from the row map
  (so constants are written into each row rather than inlined)
* a relationship MERGE must carry its own `id: row.rel`, matched with a single
  comma-separated MATCH whose endpoints each have exactly one label
* a mutation returns no rows, which is success
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from neo4j import Driver
from neo4j.exceptions import Neo4jError

from ingest import hydra
from ingest.ids import IdRegistry, hash_key, rel_id

log = logging.getLogger("ingest.load")

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "data" / "fixtures" / "graph.json"


@dataclass
class WriteStats:
    """What a load actually did, for progress output and verify_counts."""

    rows: int = 0
    batches: int = 0
    seconds: float = 0.0
    retries: int = 0

    @property
    def rows_per_second(self) -> float:
        return self.rows / self.seconds if self.seconds > 0 else 0.0

    def merge(self, other: "WriteStats") -> None:
        self.rows += other.rows
        self.batches += other.batches
        self.seconds += other.seconds
        self.retries += other.retries


@dataclass
class LoadReport:
    nodes: WriteStats = field(default_factory=WriteStats)
    edges: WriteStats = field(default_factory=WriteStats)

    def summary(self) -> str:
        return (
            f"nodes {self.nodes.rows} in {self.nodes.seconds:.1f}s "
            f"({self.nodes.rows_per_second:,.0f}/s), "
            f"edges {self.edges.rows} in {self.edges.seconds:.1f}s "
            f"({self.edges.rows_per_second:,.0f}/s)"
        )


def epoch(iso_timestamp: str) -> int:
    """Convert an ISO-8601 timestamp to an epoch int.

    Property values may only be int/float/bool/string, so every timestamp in
    the graph is an epoch integer. Computed here rather than written by hand so
    the incident's live window cannot drift from its published times.
    """
    text = iso_timestamp.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def chunk(rows: Iterable[dict], max_rows: int = hydra.MAX_BATCH_ROWS) -> Iterable[list[dict]]:
    """Split rows into batches within HydraDB's row and byte limits."""
    max_rows = min(max_rows, hydra.MAX_BATCH_ROWS)
    batch: list[dict] = []
    batch_bytes = 0
    for row in rows:
        row_bytes = len(json.dumps(row, default=str))
        too_many = len(batch) >= max_rows
        too_big = batch and batch_bytes + row_bytes > hydra.MAX_BATCH_BYTES
        if too_many or too_big:
            yield batch
            batch, batch_bytes = [], 0
        batch.append(row)
        batch_bytes += row_bytes
    if batch:
        yield batch


def node_statement(label: str, properties: Sequence[str]) -> str:
    """Build a node upsert.

    The label goes in the SET, never in the MERGE pattern, and every property
    reads from the row map.
    """
    assignments = ", ".join(f"n.{prop} = row.{prop}" for prop in properties)
    return (
        "UNWIND $rows AS row "
        "MERGE (n {id: row.vertex}) "
        f"SET n:{label}{', ' if assignments else ''}{assignments}"
    )


def edge_statement(
    rel_type: str,
    src_label: str,
    dst_label: str,
    properties: Sequence[str] = (),
) -> str:
    """Build a relationship upsert.

    Endpoints must be matched in one comma-separated MATCH, each with exactly
    one label, and the relationship needs an explicit id.
    """
    statement = (
        "UNWIND $rows AS row "
        f"MATCH (s:{src_label} {{id: row.src}}), (d:{dst_label} {{id: row.dst}}) "
        f"MERGE (s)-[r:{rel_type} {{id: row.rel}}]->(d)"
    )
    if properties:
        assignments = ", ".join(f"r.{prop} = row.{prop}" for prop in properties)
        statement += f" SET {assignments}"
    return statement


class BatchWriter:
    """Runs batched UNWIND writes over Bolt."""

    def __init__(
        self,
        driver: Driver,
        batch_rows: int = hydra.MAX_BATCH_ROWS,
        max_attempts: int = 8,
    ) -> None:
        self.driver = driver
        self.batch_rows = min(batch_rows, hydra.MAX_BATCH_ROWS)
        self.max_attempts = max_attempts

    def run_batches(self, statement: str, rows: Iterable[dict], what: str) -> WriteStats:
        stats = WriteStats()
        started = time.perf_counter()
        with hydra.session(self.driver) as session:
            for batch in chunk(rows, self.batch_rows):
                stats.retries += self._run_one(session, statement, batch, what)
                stats.rows += len(batch)
                stats.batches += 1
                if stats.batches % 50 == 0:
                    elapsed = time.perf_counter() - started
                    rate = stats.rows / elapsed if elapsed else 0
                    log.info("  %s: %d rows (%.0f/s)", what, stats.rows, rate)
        stats.seconds = time.perf_counter() - started
        return stats

    def _run_one(self, session, statement: str, batch: list[dict], what: str) -> int:
        retries = 0
        for attempt in range(1, self.max_attempts + 1):
            try:
                session.run(statement, rows=batch).consume()
                return retries
            except Neo4jError as exc:
                # An oversized batch is reported as a *transient* admission
                # error, so retrying it would loop forever. Only back off for
                # errors that a retry can actually clear.
                if "admission control" in str(exc) or attempt == self.max_attempts:
                    raise RuntimeError(
                        f"{what}: batch of {len(batch)} rows failed: {str(exc).splitlines()[0]}"
                    ) from exc
                retries += 1
                # The local object-store backend cannot do conditional writes,
                # so a storage flush that coincides with a batch fails it. The
                # flush is periodic, so backing off further lets the next
                # attempt land between flushes.
                time.sleep(min(1.0 * 2 ** (attempt - 1), 20.0))
        return retries

    def upsert_nodes(self, label: str, properties: Sequence[str], rows: Iterable[dict]) -> WriteStats:
        # No truthiness check: rows may be a generator, which is always truthy
        # and would be consumed by the test. An empty iterable simply yields
        # WriteStats(rows=0).
        return self.run_batches(node_statement(label, properties), rows, f"{label} nodes")

    def upsert_edges(
        self,
        rel_type: str,
        src_label: str,
        dst_label: str,
        properties: Sequence[str],
        rows: Iterable[dict],
    ) -> WriteStats:
        statement = edge_statement(rel_type, src_label, dst_label, properties)
        return self.run_batches(statement, rows, f"{rel_type} edges")


# --- fixture loading ------------------------------------------------------


def _node_rows(registry: IdRegistry, nodes: list[dict]) -> tuple[list[dict], list[str]]:
    """Turn fixture node records into row maps, hashing keys to ids.

    Timestamp properties arrive as ISO strings and become epoch ints here.
    Every row in a batch must carry every property the statement sets, so a
    property absent from one node is filled with a typed zero value rather than
    left missing.
    """
    rows: list[dict] = []
    properties: list[str] = []
    for node in nodes:
        key = node["key"]
        row: dict[str, Any] = {"vertex": registry.assign(key), "key": key}
        for name, value in node.get("props", {}).items():
            row[name] = epoch(value) if name.endswith("_at") and isinstance(value, str) else value
            if name not in properties:
                properties.append(name)
        rows.append(row)

    properties = ["key"] + properties
    defaults: dict[str, Any] = {}
    for row in rows:
        for prop in properties:
            if prop in row:
                defaults.setdefault(prop, row[prop])
    for row in rows:
        for prop in properties:
            if prop not in row:
                sample = defaults.get(prop, "")
                row[prop] = type(sample)() if sample is not None else ""
    return rows, properties


def _edge_directions(edge_group: dict) -> list[tuple[str, bool]]:
    """Which relationship types to write for one declared edge group.

    A group may declare `reverse_type`, which materialises the same edges
    pointing the other way. HydraDB only traverses variable-length patterns
    outward from a fixed source, so a reverse-dependency closure needs the
    reversed projection to exist as its own relationship type.
    """
    directions = [(edge_group["type"], False)]
    if edge_group.get("reverse_type"):
        directions.append((edge_group["reverse_type"], True))
    return directions


def _edge_rows(rel_type: str, edges: list[dict], *, flip: bool) -> list[dict]:
    rows = []
    for edge in edges:
        src_key, dst_key = edge["src"], edge["dst"]
        if flip:
            src_key, dst_key = dst_key, src_key
        row: dict[str, Any] = {
            "src": hash_key(src_key),
            "dst": hash_key(dst_key),
            "rel": rel_id(rel_type, src_key, dst_key),
        }
        for name, value in edge.get("props", {}).items():
            row[name] = (
                epoch(value)
                if name.endswith(("_from", "_until", "_at")) and isinstance(value, str)
                else value
            )
        rows.append(row)
    return rows


def load_fixture(path: Path = FIXTURE_PATH) -> LoadReport:
    """Load the checked-in fixture graph. Idempotent: MERGE all the way down."""
    document = json.loads(path.read_text())
    registry = IdRegistry()
    report = LoadReport()

    with hydra.connect() as driver:
        writer = BatchWriter(driver)

        # Nodes before edges: an edge batch matches both endpoints by id.
        for label, nodes in document["nodes"].items():
            rows, properties = _node_rows(registry, nodes)
            stats = writer.upsert_nodes(label, properties, rows)
            report.nodes.merge(stats)
            log.info("%-12s %4d nodes", label, stats.rows)

        for edge_group in document["edges"]:
            for rel_type, flip in _edge_directions(edge_group):
                rows = _edge_rows(rel_type, edge_group["rows"], flip=flip)
                properties = [p for p in rows[0] if p not in ("src", "dst", "rel")] if rows else []
                src_label, dst_label = edge_group["src_label"], edge_group["dst_label"]
                if flip:
                    src_label, dst_label = dst_label, src_label
                stats = writer.upsert_edges(rel_type, src_label, dst_label, properties, rows)
                report.edges.merge(stats)
                log.info("%-16s %4d edges", rel_type, stats.rows)

    return report


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Load data into HydraDB")
    parser.add_argument("command", choices=["load-fixture"])
    args = parser.parse_args()

    if args.command == "load-fixture":
        report = load_fixture()
        print(report.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
