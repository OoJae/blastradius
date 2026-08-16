"""Probe how HydraDB will let us express a reverse-dependency traversal.

The first shape probe rejected the planned blast-radius query:

    MATCH (d:Package)-[:PKG_DEPENDS_ON*1..6]->(c:Package {id: $id})

with "variable-length MATCH requires a fixed source id". The anchor has to be
the source of the pattern, but a blast radius asks the opposite question: who
points *at* this package. This script establishes which formulation works, and
prints full error messages rather than truncating them.

Toy graph, edges pointing dependent -> dependency:

    p1 -> p0      p3 -> p1      p4 -> p3      p5 -> p4
    p2 -> p0      p3 -> p2                          (diamond at p3)

So the reverse closure of p0 is {p1, p2, p3, p4, p5}, by depth:
1 -> {p1, p2}, 2 -> {p3}, 3 -> {p4}, 4 -> {p5}.
"""

from __future__ import annotations

import os

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError

from ingest.ids import pkg_id, pkg_key

BOLT_URI = os.environ.get("HYDRA_BOLT_URI", "neo4j://127.0.0.1:7687")
TOKEN = os.environ.get("HYDRA_TOKEN", "local-development-token-32-bytes")
DATABASE = os.environ.get("HYDRA_DATABASE", "default")

NAMES = [f"probe/p{i}" for i in range(6)]
EDGES = [(1, 0), (2, 0), (3, 1), (3, 2), (4, 3), (5, 4)]

NODE_UPSERT = (
    "UNWIND $rows AS row MERGE (n {id: row.vertex}) SET n:ProbePkg, n.key = row.key"
)
# Forward edge: dependent -> dependency.
EDGE_UPSERT = (
    "UNWIND $rows AS row "
    "MATCH (s:ProbePkg {id: row.src}), (d:ProbePkg {id: row.dst}) "
    "MERGE (s)-[r:PROBE_DEPENDS_ON {id: row.rel}]->(d) SET r.via = row.via"
)
# Reverse projection: dependency -> dependent. Materialised so a blast radius
# can be expressed as an outgoing traversal from a fixed source.
REVERSE_UPSERT = (
    "UNWIND $rows AS row "
    "MATCH (s:ProbePkg {id: row.src}), (d:ProbePkg {id: row.dst}) "
    "MERGE (s)-[r:PROBE_DEPENDED_BY {id: row.rel}]->(d) SET r.via = row.via"
)


def rel_id(src: int, dst: int, salt: int) -> int:
    return pkg_id(f"rel-{salt}:{src}->{dst}")


def load(session) -> None:
    session.run(
        NODE_UPSERT,
        rows=[{"vertex": pkg_id(n), "key": pkg_key(n)} for n in NAMES],
    ).consume()
    session.run(
        EDGE_UPSERT,
        rows=[
            {
                "src": pkg_id(NAMES[a]),
                "dst": pkg_id(NAMES[b]),
                "rel": rel_id(a, b, 1),
                "via": 1,
            }
            for a, b in EDGES
        ],
    ).consume()
    session.run(
        REVERSE_UPSERT,
        rows=[
            {
                "src": pkg_id(NAMES[b]),
                "dst": pkg_id(NAMES[a]),
                "rel": rel_id(b, a, 2),
                "via": 1,
            }
            for a, b in EDGES
        ],
    ).consume()


def attempt(session, name: str, cypher: str, params: dict) -> None:
    try:
        records = list(session.run(cypher, **params))
    except Neo4jError as exc:
        print(f"  REJECTED  {name}")
        print(f"            {str(exc).splitlines()[0]}")
        return
    keys = sorted(r.get("key") for r in records if r.get("key") is not None)
    short = [k.removeprefix("pkg:npm/probe/") for k in keys]
    print(f"  ACCEPTED  {name}")
    print(f"            {len(records)} row(s) {short if short else ''}")


def main() -> int:
    p0 = pkg_id(NAMES[0])
    p5 = pkg_id(NAMES[5])

    with GraphDatabase.driver(BOLT_URI, auth=("neo4j", TOKEN)) as driver:
        with driver.session(database=DATABASE) as session:
            load(session)
            print("== toy graph loaded (6 packages, 6 forward + 6 reverse edges)")

            print("\n== variable-length MATCH formulations (expect {p1..p5} for a radius)")
            attempt(
                session,
                "anchor on destination (the originally planned Q1)",
                "MATCH (d:ProbePkg)-[:PROBE_DEPENDS_ON*1..4]->(c:ProbePkg {id: $id}) "
                "RETURN DISTINCT d.key AS key",
                {"id": p0},
            )
            attempt(
                session,
                "anchor on source, left-pointing arrow",
                "MATCH (c:ProbePkg {id: $id})<-[:PROBE_DEPENDS_ON*1..4]-(d:ProbePkg) "
                "RETURN DISTINCT d.key AS key",
                {"id": p0},
            )
            attempt(
                session,
                "anchor on source over the materialised reverse edge",
                "MATCH (c:ProbePkg {id: $id})-[:PROBE_DEPENDED_BY*1..4]->(d:ProbePkg) "
                "RETURN DISTINCT d.key AS key",
                {"id": p0},
            )
            attempt(
                session,
                "forward dependencies of p5 (sanity: expect p4,p3,p1,p2,p0)",
                "MATCH (c:ProbePkg {id: $id})-[:PROBE_DEPENDS_ON*1..4]->(d:ProbePkg) "
                "RETURN DISTINCT d.key AS key",
                {"id": p5},
            )
            attempt(
                session,
                "depth 1 only, reverse edge",
                "MATCH (c:ProbePkg {id: $id})-[:PROBE_DEPENDED_BY*1..1]->(d:ProbePkg) "
                "RETURN DISTINCT d.key AS key",
                {"id": p0},
            )
            attempt(
                session,
                "count aggregate over a reverse radius",
                "MATCH (c:ProbePkg {id: $id})-[:PROBE_DEPENDED_BY*1..4]->(d:ProbePkg) "
                "RETURN count(DISTINCT d.id) AS n",
                {"id": p0},
            )

            print("\n== path procedures on the FORWARD edge (no reverse projection)")
            source_values = f"['{pkg_key(NAMES[0])}']"
            attempt(
                session,
                "SSpaths incoming from p0",
                "CALL algo.SSpaths({sourceNode: $id, relTypes: ['PROBE_DEPENDS_ON'], "
                "relDirection: 'incoming', maxLen: $maxlen, pathCount: $pc, "
                "resultLimit: $rl}) YIELD path RETURN path",
                {"id": p0, "maxlen": 4, "pc": 1, "rl": 100},
            )
            attempt(
                session,
                "MSpaths incoming, seeded by key literal",
                "CALL algo.MSpaths({sourceLabel: 'ProbePkg', sourceProperty: 'key', "
                f"sourceValues: {source_values}, relTypes: ['PROBE_DEPENDS_ON'], "
                "relDirection: 'incoming', maxLen: $maxlen, pathCount: $pc, "
                "resultLimit: $rl}) YIELD path RETURN path",
                {"maxlen": 4, "pc": 1, "rl": 100},
            )
            attempt(
                session,
                "MSpaths with sourceValues as a driver parameter",
                "CALL algo.MSpaths({sourceLabel: 'ProbePkg', sourceProperty: 'key', "
                "sourceValues: $vals, relTypes: ['PROBE_DEPENDS_ON'], "
                "relDirection: 'incoming', maxLen: $maxlen, pathCount: $pc, "
                "resultLimit: $rl}) YIELD path RETURN path",
                {"vals": [pkg_key(NAMES[0])], "maxlen": 4, "pc": 1, "rl": 100},
            )
            attempt(
                session,
                "MSpaths incoming, pathCount 0 (all minimum-weight paths)",
                "CALL algo.MSpaths({sourceLabel: 'ProbePkg', sourceProperty: 'key', "
                f"sourceValues: {source_values}, relTypes: ['PROBE_DEPENDS_ON'], "
                "relDirection: 'incoming', maxLen: $maxlen, pathCount: 0, "
                "resultLimit: $rl}) YIELD path RETURN path",
                {"maxlen": 4, "rl": 100},
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
