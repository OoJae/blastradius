"""Check HydraDB's answers against the hand-computed fixture answers.

Requires a running node with the fixture loaded: `just up && just ingest-demo`,
then `just test-live`. These are marked `live` and excluded from a plain
`pytest` run.

The expectations come from data/fixtures/expected_answers.json, which was
worked out by reading the edge list (see data/fixtures/README.md). Nothing here
compares HydraDB against itself.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ingest import hydra
from ingest.ids import hash_key

pytestmark = pytest.mark.live

FIXTURES = Path(__file__).resolve().parent.parent / "data" / "fixtures"
EXPECTED = json.loads((FIXTURES / "expected_answers.json").read_text())

# Bounds must be literals in a variable-length pattern, so depths come from a
# fixed whitelist rather than being interpolated from anything user-supplied.
ALLOWED_DEPTHS = (1, 2, 3, 4, 5, 6, 7, 8)


@pytest.fixture(scope="module")
def session():
    with hydra.connect() as driver:
        with hydra.session(driver) as sess:
            yield sess


def radius(session, package_key: str, depth: int) -> set[str]:
    assert depth in ALLOWED_DEPTHS
    records = session.run(
        f"MATCH (c:Package {{id: $id}})-[:PKG_DEPENDED_BY*1..{depth}]->(d:Package) "
        "RETURN DISTINCT d.key AS key",
        id=hash_key(package_key),
    )
    return {record["key"] for record in records}


@pytest.mark.parametrize("package_key", sorted(EXPECTED["blast_radius"]))
def test_blast_radius_by_depth(session, package_key: str) -> None:
    for depth_text, expected in EXPECTED["blast_radius"][package_key].items():
        got = radius(session, package_key, int(depth_text))
        assert got == set(expected), f"{package_key} at depth {depth_text}"


def test_diamond_is_counted_once(session) -> None:
    # app-kit reaches flux-core through both flux-router and flux-query.
    got = radius(session, "pkg:npm/flux-core", 2)
    assert len([k for k in got if k == "pkg:npm/app-kit"]) == 1
    assert len(got) == 5


def test_packages_outside_the_incident_never_appear(session) -> None:
    got = radius(session, "pkg:npm/flux-core", 8)
    for key in EXPECTED["never_in_radius"]["pkg:npm/flux-core"]:
        assert key not in got


def test_minimum_depths(session) -> None:
    expected = EXPECTED["min_depth"]["pkg:npm/flux-core"]
    seen: dict[str, int] = {}
    for depth in (1, 2, 3):
        for key in radius(session, "pkg:npm/flux-core", depth):
            seen.setdefault(key, depth)
    assert seen == expected


def test_multi_source_radius_deduplicates_across_seeds(session) -> None:
    multi = EXPECTED["multi_source_blast_radius"]
    for depth_text in ("1", "2", "3"):
        union: set[str] = set()
        for seed in multi["seeds"]:
            union |= radius(session, seed, int(depth_text))
        assert union == set(multi[depth_text]), f"depth {depth_text}"


def test_multi_source_via_one_mspaths_call(session) -> None:
    """The whole incident in a single server-side call, seeded with every
    compromised package at once."""
    multi = EXPECTED["multi_source_blast_radius"]
    seeds = hydra.cypher_string_list(multi["seeds"])
    records = session.run(
        "CALL algo.MSpaths({sourceLabel: 'Package', sourceProperty: 'key', "
        f"sourceValues: {seeds}, relTypes: ['PKG_DEPENDS_ON'], "
        "relDirection: 'incoming', maxLen: 3, pathCount: $pathcount, "
        "resultLimit: 1000}) YIELD path RETURN path",
        pathcount=500,
    )
    reached = {
        node.get("key")
        for record in records
        for node in record["path"].nodes
        if node.get("key")
    }
    assert reached - set(multi["seeds"]) == set(multi["3"])


@pytest.mark.parametrize("version_key", EXPECTED["version_seeded_returns_nothing"])
def test_version_seeds_find_no_package_paths(session, version_key: str) -> None:
    # Why the incident query seeds with package keys, not the 84 version keys.
    records = list(
        session.run(
            "CALL algo.MSpaths({sourceLabel: 'Version', sourceProperty: 'key', "
            f"sourceValues: {hydra.cypher_string_list([version_key])}, "
            "relTypes: ['PKG_DEPENDS_ON'], relDirection: 'incoming', maxLen: 3, "
            "pathCount: $pathcount, resultLimit: 1000}) YIELD path RETURN path",
            pathcount=500,
        )
    )
    assert records == []


@pytest.mark.parametrize("package_key", sorted(EXPECTED["maintainer_overlap"]))
def test_maintainer_overlap(session, package_key: str) -> None:
    expected = EXPECTED["maintainer_overlap"][package_key]

    maintainers = {
        record["key"]
        for record in session.run(
            "MATCH (m:Maintainer)-[:MAINTAINS]->(p:Package {id: $id}) RETURN m.key AS key",
            id=hash_key(package_key),
        )
    }
    assert maintainers == set(expected["maintainers"])

    siblings: set[str] = set()
    for maintainer_key in maintainers:
        siblings |= {
            record["key"]
            for record in session.run(
                "MATCH (m:Maintainer {id: $id})-[:MAINTAINS]->(q:Package) RETURN q.key AS key",
                id=hash_key(maintainer_key),
            )
        }
    assert siblings - {package_key} == set(expected["sibling_packages"])


def test_maintainer_overlap_is_not_the_dependency_closure(session) -> None:
    # orm-lite shares an owner with flux-core but does not depend on it.
    assert "pkg:npm/orm-lite" in EXPECTED["maintainer_overlap"]["pkg:npm/flux-core"][
        "sibling_packages"
    ]
    assert "pkg:npm/orm-lite" not in radius(session, "pkg:npm/flux-core", 8)


@pytest.mark.parametrize("package_key", sorted(EXPECTED["typosquats"]))
def test_typosquats(session, package_key: str) -> None:
    got = [
        {"key": record["key"], "distance": record["distance"]}
        for record in session.run(
            "MATCH (p:Package {id: $id})-[s:SIMILAR_NAME]->(q:Package) "
            "RETURN q.key AS key, s.distance AS distance ORDER BY distance",
            id=hash_key(package_key),
        )
    ]
    assert got == EXPECTED["typosquats"][package_key]


def test_autocomplete_prefix_search(session) -> None:
    for prefix, expected in EXPECTED["autocomplete"].items():
        got = sorted(
            record["key"]
            for record in session.run(
                "MATCH (p:Package) WHERE p.name STARTS WITH $prefix "
                "RETURN p.key AS key LIMIT 10",
                prefix=prefix,
            )
        )
        assert got == sorted(expected)


@pytest.mark.parametrize(
    ("version_key", "advisory_key"), sorted(EXPECTED["advisory_direct_hits"].items())
)
def test_advisory_hits_carry_the_live_window(session, version_key, advisory_key) -> None:
    window = EXPECTED["compromised"]
    records = list(
        session.run(
            "MATCH (a:Advisory)-[r:AFFECTS]->(v:Version {id: $id}) "
            "RETURN a.key AS advisory, r.live_from AS live_from, r.live_until AS live_until",
            id=hash_key(version_key),
        )
    )
    if advisory_key is None:
        assert records == []
        return

    assert len(records) == 1
    record = records[0]
    assert record["advisory"] == advisory_key

    from ingest.load import epoch

    assert record["live_from"] == epoch(window["live_from"])
    assert record["live_until"] == epoch(window["live_until"])
    assert record["live_until"] - record["live_from"] == 360  # six minutes


def test_node_and_edge_counts(session) -> None:
    counts = EXPECTED["counts"]
    for label in ("Package", "Version", "Maintainer", "Advisory", "Service"):
        got = session.run(f"MATCH (n:{label}) RETURN count(*) AS n").single()["n"]
        assert got == counts[label], label


def test_compromised_versions_are_flagged(session) -> None:
    got = {
        record["key"]
        for record in session.run(
            "MATCH (v:Version) WHERE v.compromised = true RETURN v.key AS key"
        )
    }
    assert got == set(EXPECTED["compromised"]["versions"])
