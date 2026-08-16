"""Probe single-hop and aggregate shapes that queries Q3-Q5 depend on.

The variable-length restriction ("fixed source id") does not obviously apply to
single-hop patterns, but maintainer overlap and advisory lookups are both
naturally written destination-anchored, so it is worth knowing before the
schema is settled.
"""

from __future__ import annotations

import os

from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError

from ingest.ids import mnt_id, mnt_key, pkg_id, pkg_key

BOLT_URI = os.environ.get("HYDRA_BOLT_URI", "neo4j://127.0.0.1:7687")
TOKEN = os.environ.get("HYDRA_TOKEN", "local-development-token-32-bytes")
DATABASE = os.environ.get("HYDRA_DATABASE", "default")

PKGS = ["probe/p0", "probe/p1"]
MNT = "probe-alice"


def attempt(session, name: str, cypher: str, params: dict) -> None:
    try:
        records = list(session.run(cypher, **params))
    except Neo4jError as exc:
        print(f"  REJECTED  {name}")
        print(f"            {str(exc).splitlines()[0][:180]}")
        return
    print(f"  ACCEPTED  {name}  -> {[dict(r) for r in records][:4]}")


def main() -> int:
    with GraphDatabase.driver(BOLT_URI, auth=("neo4j", TOKEN)) as driver:
        with driver.session(database=DATABASE) as session:
            session.run(
                "UNWIND $rows AS row MERGE (n {id: row.vertex}) "
                "SET n:ProbeMnt, n.key = row.key",
                rows=[{"vertex": mnt_id(MNT), "key": mnt_key(MNT)}],
            ).consume()
            session.run(
                "UNWIND $rows AS row "
                "MATCH (s:ProbeMnt {id: row.src}), (d:ProbePkg {id: row.dst}) "
                "MERGE (s)-[r:PROBE_MAINTAINS {id: row.rel}]->(d) SET r.n = row.n",
                rows=[
                    {
                        "src": mnt_id(MNT),
                        "dst": pkg_id(p),
                        "rel": pkg_id(f"maint:{MNT}->{p}"),
                        "n": 1,
                    }
                    for p in PKGS
                ],
            ).consume()
            print("== maintainer probe data loaded")

            print("\n== single-hop patterns")
            attempt(
                session,
                "destination-anchored single hop (Q4 step 1)",
                "MATCH (m:ProbeMnt)-[:PROBE_MAINTAINS]->(p:ProbePkg {id: $id}) "
                "RETURN m.key AS maintainer",
                {"id": pkg_id(PKGS[0])},
            )
            attempt(
                session,
                "source-anchored single hop (Q4 step 2)",
                "MATCH (m:ProbeMnt {id: $id})-[:PROBE_MAINTAINS]->(p:ProbePkg) "
                "RETURN p.key AS pkg",
                {"id": mnt_id(MNT)},
            )
            attempt(
                session,
                "destination-anchored with edge properties returned (Q3 advisory)",
                "MATCH (m:ProbeMnt)-[r:PROBE_MAINTAINS]->(p:ProbePkg {id: $id}) "
                "RETURN m.key AS maintainer, r.n AS n",
                {"id": pkg_id(PKGS[0])},
            )
            attempt(
                session,
                "fixed-depth-1 var-length, destination anchored",
                "MATCH (m:ProbeMnt)-[:PROBE_MAINTAINS*1..1]->(p:ProbePkg {id: $id}) "
                "RETURN m.key AS maintainer",
                {"id": pkg_id(PKGS[0])},
            )

            print("\n== aggregates and ordering over a radius")
            attempt(
                session,
                "count(*) over a source-anchored radius",
                "MATCH (c:ProbePkg {id: $id})-[:PROBE_DEPENDED_BY*1..4]->(d:ProbePkg) "
                "RETURN count(*) AS n",
                {"id": pkg_id("probe/p0")},
            )
            attempt(
                session,
                "DISTINCT projection with LIMIT (the shape Q1 will use)",
                "MATCH (c:ProbePkg {id: $id})-[:PROBE_DEPENDED_BY*1..4]->(d:ProbePkg) "
                "RETURN DISTINCT d.key AS key, d.id AS id LIMIT 5000",
                {"id": pkg_id("probe/p0")},
            )
            attempt(
                session,
                "collect over a radius",
                "MATCH (c:ProbePkg {id: $id})-[:PROBE_DEPENDED_BY*1..4]->(d:ProbePkg) "
                "RETURN collect(d.key) AS keys",
                {"id": pkg_id("probe/p0")},
            )

            print("\n== cleanup")
            session.run(
                "UNWIND $rows AS row MATCH (n {id: row.vertex}) DETACH DELETE n",
                rows=[{"vertex": mnt_id(MNT)}]
                + [{"vertex": pkg_id(f"probe/p{i}")} for i in range(6)],
            ).consume()
            print("  probe nodes deleted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
