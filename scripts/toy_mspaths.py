"""Verify path-procedure semantics on a small graph with known answers.

Answers the questions the product design depends on:

1. does an incoming-direction traversal return the reverse closure?
2. does the materialised reverse edge give the same answer outgoing?
3. must `sourceValues` be an inline literal rather than a driver parameter?
4. does the seed label filter only the seeds, or every node on the path?
5. seeding with Version keys over a Package-to-Package edge: does it return
   nothing? (Q2 has 84 compromised *versions* across 42 *packages*; if Version
   seeds return nothing then Q2 must seed with the package keys.)

Graph (edges point dependent -> dependency), plus a materialised reverse of
each edge and one Version node attached to p0:

    p1 -> p0     p3 -> p1     p4 -> p3     p5 -> p4
    p2 -> p0     p3 -> p2                        (diamond at p3)

Reverse closure of p0 by depth: 1 -> {p1,p2}, 2 -> {p3}, 3 -> {p4}, 4 -> {p5}.
"""

from __future__ import annotations

import sys

from neo4j.exceptions import Neo4jError

from ingest import hydra
from ingest.ids import hash_key, pkg_key, rel_id, ver_key
from ingest.load import BatchWriter

NAMES = [f"toy/p{i}" for i in range(6)]
EDGES = [(1, 0), (2, 0), (3, 1), (3, 2), (4, 3), (5, 4)]
VERSION = ("toy/p0", "1.0.0")

EXPECTED_BY_DEPTH = {
    1: {"toy/p1", "toy/p2"},
    2: {"toy/p1", "toy/p2", "toy/p3"},
    3: {"toy/p1", "toy/p2", "toy/p3", "toy/p4"},
    4: {"toy/p1", "toy/p2", "toy/p3", "toy/p4", "toy/p5"},
}

# pathCount ranks paths by weight and keeps the cheapest N, so it is a
# shortest-paths budget rather than a traversal depth. A value comfortably
# above the number of simple paths in this graph makes the procedure enumerate
# all of them, which is what a closure check needs.
PATH_COUNT_ALL = 200

results: list[tuple[bool, str, str]] = []


def check(passed: bool, name: str, detail: str = "") -> None:
    results.append((passed, name, detail))
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
    if detail:
        print(f"        {detail}")


def short(key: str) -> str:
    return key.removeprefix("pkg:npm/").removeprefix("ver:npm/")


def load(writer: BatchWriter) -> None:
    writer.upsert_nodes(
        "ToyPkg",
        ["key"],
        [{"vertex": hash_key(pkg_key(n)), "key": pkg_key(n)} for n in NAMES],
    )
    writer.upsert_nodes(
        "ToyVer",
        ["key"],
        [{"vertex": hash_key(ver_key(*VERSION)), "key": ver_key(*VERSION)}],
    )

    def edge_rows(rel_type, pairs):
        rows = []
        for src_name, dst_name in pairs:
            src, dst = pkg_key(src_name), pkg_key(dst_name)
            rows.append(
                {
                    "src": hash_key(src),
                    "dst": hash_key(dst),
                    "rel": rel_id(rel_type, src, dst),
                    "via_versions": 1,
                }
            )
        return rows

    forward = [(NAMES[a], NAMES[b]) for a, b in EDGES]
    writer.upsert_edges(
        "TOY_DEPENDS_ON", "ToyPkg", "ToyPkg", ["via_versions"], edge_rows("TOY_DEPENDS_ON", forward)
    )
    reverse = [(b, a) for a, b in forward]
    writer.upsert_edges(
        "TOY_DEPENDED_BY", "ToyPkg", "ToyPkg", ["via_versions"], edge_rows("TOY_DEPENDED_BY", reverse)
    )

    ver, pkg = ver_key(*VERSION), pkg_key(VERSION[0])
    writer.upsert_edges(
        "TOY_VERSION_OF",
        "ToyVer",
        "ToyPkg",
        [],
        [{"src": hash_key(ver), "dst": hash_key(pkg), "rel": rel_id("TOY_VERSION_OF", ver, pkg)}],
    )


def terminals(records) -> set[str]:
    """Every non-seed node appearing on a returned path."""
    found: set[str] = set()
    for record in records:
        for node in record["path"].nodes:
            key = node.get("key")
            if key:
                found.add(short(key))
    return found


def main() -> int:
    p0 = pkg_key(NAMES[0])

    with hydra.connect() as driver:
        load(BatchWriter(driver))
        print("== toy graph loaded (6 packages, 1 version, forward + reverse edges)\n")

        with hydra.session(driver) as session:
            print("== 1. MSpaths incoming over the forward edge")
            for depth, expected in EXPECTED_BY_DEPTH.items():
                records = list(
                    session.run(
                        "CALL algo.MSpaths({sourceLabel: 'ToyPkg', sourceProperty: 'key', "
                        f"sourceValues: {hydra.cypher_string_list([p0])}, "
                        "relTypes: ['TOY_DEPENDS_ON'], relDirection: 'incoming', "
                        "maxLen: $maxlen, pathCount: $pathcount, resultLimit: 1000}) "
                        "YIELD path RETURN path",
                        maxlen=depth,
                        pathcount=PATH_COUNT_ALL,
                    )
                )
                got = terminals(records) - {"toy/p0"}
                check(
                    got == expected,
                    f"depth {depth} closure == {sorted(expected)}",
                    "" if got == expected else f"got {sorted(got)}",
                )

            print("\n== 1b. pathCount selects the N shortest paths, not a node set")
            observed = {}
            for path_count in (0, 1, 3, 5, PATH_COUNT_ALL):
                records = list(
                    session.run(
                        "CALL algo.MSpaths({sourceLabel: 'ToyPkg', sourceProperty: 'key', "
                        f"sourceValues: {hydra.cypher_string_list([p0])}, "
                        "relTypes: ['TOY_DEPENDS_ON'], relDirection: 'incoming', "
                        "maxLen: 4, pathCount: $pathcount, resultLimit: 1000}) "
                        "YIELD path RETURN path",
                        pathcount=path_count,
                    )
                )
                observed[path_count] = sorted(len(r["path"].relationships) for r in records)
            check(
                observed[0] == [1, 1],
                "pathCount 0 returns only minimum-weight (1-hop) paths",
                f"lengths {observed[0]}",
            )
            check(
                observed[1] == [1] and observed[3] == [1, 1, 2],
                "pathCount N returns the N shortest paths",
                f"1 -> {observed[1]}, 3 -> {observed[3]}, 5 -> {observed[5]}",
            )
            check(
                observed[PATH_COUNT_ALL] == [1, 1, 2, 2, 3, 3, 4, 4],
                "a large pathCount enumerates every simple path (8 here, not 5 nodes)",
                f"lengths {observed[PATH_COUNT_ALL]}",
            )

            print("\n== 2. var-length MATCH over the materialised reverse edge")
            for depth, expected in EXPECTED_BY_DEPTH.items():
                records = list(
                    session.run(
                        "MATCH (c:ToyPkg {id: $id})"
                        f"-[:TOY_DEPENDED_BY*1..{depth}]->(d:ToyPkg) "
                        "RETURN DISTINCT d.key AS key",
                        id=hash_key(p0),
                    )
                )
                got = {short(r["key"]) for r in records}
                check(
                    got == expected,
                    f"depth {depth} closure == {sorted(expected)}",
                    "" if got == expected else f"got {sorted(got)}",
                )

            print("\n== 3. sourceValues must be an inline literal")
            try:
                session.run(
                    "CALL algo.MSpaths({sourceLabel: 'ToyPkg', sourceProperty: 'key', "
                    "sourceValues: $vals, relTypes: ['TOY_DEPENDS_ON'], "
                    "relDirection: 'incoming', maxLen: 4, pathCount: 1, resultLimit: 100}) "
                    "YIELD path RETURN path",
                    vals=[p0],
                ).consume()
            except Neo4jError as exc:
                check(True, "driver parameter rejected", str(exc).splitlines()[0][:110])
            else:
                check(False, "driver parameter rejected", "it was accepted after all")

            print("\n== 4. the label filter applies to seeds, not to path interiors")
            mixed = list(
                session.run(
                    "CALL algo.MSpaths({sourceLabel: 'ToyPkg', sourceProperty: 'key', "
                    f"sourceValues: {hydra.cypher_string_list([p0, ver_key(*VERSION)])}, "
                    "relTypes: ['TOY_DEPENDS_ON'], relDirection: 'incoming', "
                    "maxLen: 4, pathCount: $pathcount, resultLimit: 1000}) YIELD path RETURN path",
                    pathcount=PATH_COUNT_ALL,
                )
            )
            got = terminals(mixed) - {"toy/p0"}
            check(
                got == EXPECTED_BY_DEPTH[4],
                "a Version key mixed into Package seeds is ignored",
                f"got {sorted(got)}",
            )

            print("\n== 5. Version-seeded traversal over a Package edge returns nothing")
            version_seeded = list(
                session.run(
                    "CALL algo.MSpaths({sourceLabel: 'ToyVer', sourceProperty: 'key', "
                    f"sourceValues: {hydra.cypher_string_list([ver_key(*VERSION)])}, "
                    "relTypes: ['TOY_DEPENDS_ON'], relDirection: 'incoming', "
                    "maxLen: 4, pathCount: $pathcount, resultLimit: 1000}) YIELD path RETURN path",
                    pathcount=PATH_COUNT_ALL,
                )
            )
            check(
                len(version_seeded) == 0,
                "Q2 must seed with package keys, not version keys",
                f"{len(version_seeded)} path(s) returned",
            )

            print("\n== 6. outgoing direction still reaches dependencies")
            outgoing = list(
                session.run(
                    "CALL algo.MSpaths({sourceLabel: 'ToyPkg', sourceProperty: 'key', "
                    f"sourceValues: {hydra.cypher_string_list([pkg_key(NAMES[5])])}, "
                    "relTypes: ['TOY_DEPENDS_ON'], relDirection: 'outgoing', "
                    "maxLen: 4, pathCount: $pathcount, resultLimit: 1000}) YIELD path RETURN path",
                    pathcount=PATH_COUNT_ALL,
                )
            )
            got = terminals(outgoing) - {"toy/p5"}
            expected = {"toy/p4", "toy/p3", "toy/p1", "toy/p2", "toy/p0"}
            check(got == expected, "dependencies of p5", f"got {sorted(got)}")

            print("\n== cleanup")
            session.run(
                "UNWIND $rows AS row MATCH (n {id: row.vertex}) DETACH DELETE n",
                rows=[{"vertex": hash_key(pkg_key(n))} for n in NAMES]
                + [{"vertex": hash_key(ver_key(*VERSION))}],
            ).consume()
            print("  toy nodes deleted")

    failed = [name for ok, name, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED: " + "; ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
