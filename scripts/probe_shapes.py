"""Probe which batched-write statement shapes HydraDB actually accepts.

There is no network-reachable EXPLAIN, so the only way to know whether a
statement parses is to run it. This settles the open questions before the
loader is written around a guess:

* must a batched relationship MERGE carry an explicit relationship id?
* can two endpoints be matched with separate MATCH clauses, or only a
  comma-separated one?
* is a batched upsert allowed to SET a literal, or must every value come
  from the row map?
* what is the real batch-row ceiling?

Results are printed as a table and pasted into docs/VERIFICATION.md.
"""

from __future__ import annotations

import os

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError

BOLT_URI = os.environ.get("HYDRA_BOLT_URI", "neo4j://127.0.0.1:7687")
TOKEN = os.environ.get("HYDRA_TOKEN", "local-development-token-32-bytes")
DATABASE = os.environ.get("HYDRA_DATABASE", "default")

BASE = 910_000_000

PROBES: list[tuple[str, str, dict]] = [
    (
        "node upsert: MERGE by id + SET label and props",
        "UNWIND $rows AS row MERGE (n {id: row.vertex}) "
        "SET n:Probe, n.key = row.key, n.rank = row.rank",
        {"rows": [{"vertex": BASE + 1, "key": "probe:a", "rank": 1}]},
    ),
    (
        "node upsert: label inside the MERGE pattern",
        "UNWIND $rows AS row MERGE (n:Probe {id: row.vertex}) SET n.key = row.key",
        {"rows": [{"vertex": BASE + 2, "key": "probe:b"}]},
    ),
    (
        "node upsert: SET a literal constant",
        "UNWIND $rows AS row MERGE (n {id: row.vertex}) "
        "SET n:Probe, n.key = row.key, n.compromised = true",
        {"rows": [{"vertex": BASE + 3, "key": "probe:c"}]},
    ),
    (
        "node upsert: boolean carried in the row instead",
        "UNWIND $rows AS row MERGE (n {id: row.vertex}) "
        "SET n:Probe, n.key = row.key, n.compromised = row.compromised",
        {"rows": [{"vertex": BASE + 4, "key": "probe:d", "compromised": True}]},
    ),
    (
        "edge: comma-separated endpoint MATCH, MERGE with relationship id",
        "UNWIND $rows AS row "
        "MATCH (s:Probe {id: row.src}), (d:Probe {id: row.dst}) "
        "MERGE (s)-[r:PROBE_EDGE {id: row.rel}]->(d) SET r.weight = row.weight",
        {"rows": [{"src": BASE + 1, "dst": BASE + 4, "rel": BASE + 100, "weight": 2}]},
    ),
    (
        "edge: MERGE without a relationship id",
        "UNWIND $rows AS row "
        "MATCH (s:Probe {id: row.src}), (d:Probe {id: row.dst}) "
        "MERGE (s)-[r:PROBE_EDGE]->(d) SET r.weight = row.weight",
        {"rows": [{"src": BASE + 1, "dst": BASE + 4, "weight": 3}]},
    ),
    (
        "edge: MERGE without a relationship id and without SET",
        "UNWIND $rows AS row "
        "MATCH (s:Probe {id: row.src}), (d:Probe {id: row.dst}) "
        "MERGE (s)-[:PROBE_EDGE]->(d)",
        {"rows": [{"src": BASE + 4, "dst": BASE + 1}]},
    ),
    (
        "edge: two separate MATCH clauses for the endpoints",
        "UNWIND $rows AS row "
        "MATCH (s:Probe {id: row.src}) MATCH (d:Probe {id: row.dst}) "
        "MERGE (s)-[r:PROBE_EDGE {id: row.rel}]->(d) SET r.weight = row.weight",
        {"rows": [{"src": BASE + 1, "dst": BASE + 4, "rel": BASE + 101, "weight": 4}]},
    ),
    (
        "edge: unlabelled endpoints",
        "UNWIND $rows AS row "
        "MATCH (s {id: row.src}), (d {id: row.dst}) "
        "MERGE (s)-[r:PROBE_EDGE {id: row.rel}]->(d) SET r.weight = row.weight",
        {"rows": [{"src": BASE + 1, "dst": BASE + 4, "rel": BASE + 102, "weight": 5}]},
    ),
    (
        "read: var-length with a literal bound",
        "MATCH (d:Probe)-[:PROBE_EDGE*1..3]->(c:Probe {id: $id}) "
        "RETURN DISTINCT d.key AS key",
        {"id": BASE + 4},
    ),
    (
        "read: var-length with a parameter bound",
        "MATCH (d:Probe)-[:PROBE_EDGE*1..$depth]->(c:Probe {id: $id}) "
        "RETURN DISTINCT d.key AS key",
        {"id": BASE + 4, "depth": 3},
    ),
    (
        "read: STARTS WITH prefix search",
        "MATCH (p:Probe) WHERE p.key STARTS WITH $prefix RETURN p.key AS key LIMIT 5",
        {"prefix": "probe:"},
    ),
    (
        "read: count aggregate",
        "MATCH (p:Probe) RETURN count(*) AS n",
        {},
    ),
]

BATCH_SIZES = [1000, 1024, 1025, 2000, 5000]


def run_probe(session, name: str, cypher: str, params: dict) -> bool:
    try:
        records = list(session.run(cypher, **params))
    except Neo4jError as exc:
        message = str(exc).split("\n")[0][:150]
        print(f"  REJECTED  {name}\n            {message}")
        return False
    detail = f"{len(records)} row(s)" if records else "empty envelope"
    print(f"  ACCEPTED  {name}  -> {detail}")
    return True


def probe_batch_ceiling(session) -> None:
    print("\n== batch-row ceiling")
    for size in BATCH_SIZES:
        rows = [
            {"vertex": BASE + 1000 + i, "key": f"probe:batch-{i}"} for i in range(size)
        ]
        try:
            session.run(
                "UNWIND $rows AS row MERGE (n {id: row.vertex}) SET n:Probe, n.key = row.key",
                rows=rows,
            ).consume()
        except Neo4jError as exc:
            message = str(exc).split("\n")[0][:150]
            print(f"  REJECTED  {size} rows\n            {message}")
        else:
            print(f"  ACCEPTED  {size} rows")


def cleanup(session) -> None:
    session.run(
        "UNWIND $rows AS row MATCH (n {id: row.vertex}) DETACH DELETE n",
        rows=[{"vertex": BASE + i} for i in range(200)],
    ).consume()
    for start in range(1000, 6000, 500):
        session.run(
            "UNWIND $rows AS row MATCH (n {id: row.vertex}) DETACH DELETE n",
            rows=[{"vertex": BASE + i} for i in range(start, start + 500)],
        ).consume()


def main() -> int:
    with GraphDatabase.driver(BOLT_URI, auth=("neo4j", TOKEN)) as driver:
        with driver.session(database=DATABASE) as session:
            print("== statement shapes")
            for name, cypher, params in PROBES:
                run_probe(session, name, cypher, params)
            probe_batch_ceiling(session)
            print("\n== cleanup")
            cleanup(session)
            print("  probe nodes deleted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
