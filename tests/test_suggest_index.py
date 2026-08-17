"""The autocomplete index.

It exists because the graph-native prefix search costs ~5 s on the demo slice.
That trade is only defensible if the index cannot invent anything, so the tests
below care as much about what it refuses to return as what it finds.
"""

from __future__ import annotations

from api.digest import GraphDigest

ROWS = [
    {"name": "react", "is_popular": True},
    {"name": "react-dom", "is_popular": True},
    {"name": "react-router", "is_popular": True},
    {"name": "reactive-thing", "is_popular": False},
    {"name": "@tanstack/react-router", "is_popular": True},
    {"name": "@tanstack/react-query", "is_popular": True},
    {"name": "@uipath/common", "is_popular": False},
    {"name": "lodash", "is_popular": True},
]


def build() -> GraphDigest:
    digest = GraphDigest()
    digest.names = sorted(row["name"] for row in ROWS)
    digest.popular = {row["name"] for row in ROWS if row["is_popular"]}
    keyed: list[tuple[str, str]] = []
    for name in digest.names:
        keyed.append((name.lower(), name))
        if name.startswith("@") and "/" in name:
            keyed.append((name.split("/", 1)[1].lower(), name))
    keyed.sort()
    digest._keys = [k for k, _ in keyed]
    digest._targets = [t for _, t in keyed]
    return digest


def names_for(prefix: str, limit: int = 10) -> list[str]:
    return [hit["name"] for hit in build().suggest(prefix, limit=limit)]


def test_finds_names_by_prefix() -> None:
    found = names_for("react")
    assert "react" in found and "react-dom" in found and "react-router" in found


def test_scoped_packages_are_findable_by_their_bare_name() -> None:
    # Nobody types the scope first. `react-router` has to surface the scoped
    # package as well as the unscoped one.
    found = names_for("react-router")
    assert "@tanstack/react-router" in found
    assert "react-router" in found


def test_scope_prefix_also_works() -> None:
    found = names_for("@tanstack/")
    assert set(found) == {"@tanstack/react-router", "@tanstack/react-query"}


def test_exact_name_ranks_first() -> None:
    assert names_for("react")[0] == "react"


def test_popular_packages_outrank_obscure_ones() -> None:
    found = names_for("react")
    assert found.index("react-dom") < found.index("reactive-thing")


def test_limit_is_respected() -> None:
    assert len(names_for("r", limit=2)) == 2


def test_a_prefix_matching_nothing_returns_nothing() -> None:
    assert names_for("zzzz") == []


def test_empty_prefix_returns_nothing_rather_than_everything() -> None:
    # A blank box should not dump the whole graph.
    assert names_for("") == []


def test_the_index_cannot_invent_a_name() -> None:
    # The load-bearing property: it can only ever return rows HydraDB gave us.
    known = {row["name"] for row in ROWS}
    for prefix in ("a", "r", "@", "l", "reac", "@uipath"):
        assert set(names_for(prefix, limit=50)) <= known


def test_lookup_is_case_insensitive() -> None:
    assert "@tanstack/react-router" in names_for("@TanStack/")
    assert "lodash" in names_for("LOD")


def test_membership_checks_use_the_same_data() -> None:
    digest = build()
    assert digest.has_package("react")
    assert digest.has_package("@tanstack/react-router")
    assert not digest.has_package("never-published")
