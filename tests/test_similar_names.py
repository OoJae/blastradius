"""Typosquat edge construction.

Runs offline. The cases that matter are the ones that keep the edge set
meaningful: siblings inside a scope are not squats, a name is not its own
squat, and short names cannot flood the graph.
"""

from __future__ import annotations

from ingest.sources.similar_names import (
    SimilarPair,
    same_scope,
    scope_of,
    similar_pairs,
)

CORPUS = [
    "react",
    "raect",
    "reactt",
    "preact",
    "lodash",
    "lodahs",
    "express",
    "@uipath/foo-bar",
    "@uipath/foo-baz",
    "@tanstack/react-router",
    "@tanstack/react-rooter",
    # A lookalike under a scope the real owner does not control -- this is what
    # a typosquat of a scoped package actually looks like. One edit from the
    # real name, so it is inside the distance cutoff.
    "@tanstak/react-router",
]


def pairs_for(queries, **kwargs) -> set[frozenset[str]]:
    """Pairs as unordered sets.

    Results are canonicalised as `a < b`, so asserting on an ordered tuple
    encodes lexicographic trivia into the test rather than the behaviour.
    """
    found, _ = similar_pairs(queries, CORPUS, rank={}, **kwargs)
    return {frozenset((p.a, p.b)) for p in found}


def test_finds_single_edit_neighbours() -> None:
    found = pairs_for(["react"])
    assert frozenset(("raect", "react")) in found
    assert frozenset(("react", "reactt")) in found


def test_a_name_is_never_its_own_squat() -> None:
    for pair in pairs_for(["react"]):
        assert len(pair) == 2


def test_packages_in_the_same_scope_are_siblings_not_squats() -> None:
    # @uipath/foo-bar and @uipath/foo-baz differ by one character but are
    # published by the same owner. Most campaign victims are scoped, so without
    # this the edge set would be mostly noise.
    assert same_scope("@uipath/foo-bar", "@uipath/foo-baz")
    found = pairs_for(["@uipath/foo-bar"])
    assert frozenset(("@uipath/foo-bar", "@uipath/foo-baz")) not in found


def test_squats_under_a_different_scope_are_found() -> None:
    # An attacker cannot publish into a scope they do not own, so a real squat
    # of a scoped package lives under a *different* scope. That case must
    # survive the same-scope filter.
    found = pairs_for(["@tanstack/react-router"])
    assert frozenset(("@tanstak/react-router", "@tanstack/react-router")) in found

    # And the sibling under the same scope is still suppressed.
    assert frozenset(("@tanstack/react-rooter", "@tanstack/react-router")) not in found


def test_pairs_are_canonical_so_both_ends_dedupe() -> None:
    found, _ = similar_pairs(["react", "raect"], CORPUS, rank={})
    keys = [(p.a, p.b) for p in found]
    assert len(keys) == len(set(keys))
    for pair in found:
        assert pair.a < pair.b


def test_distance_cutoff_is_respected() -> None:
    found, _ = similar_pairs(["react"], CORPUS, rank={})
    for pair in found:
        assert 1 <= pair.distance <= 2
    assert not any("express" in (p.a, p.b) for p in found)


def test_neighbour_cap_is_reported_not_silent() -> None:
    _, stats = similar_pairs(["react"], CORPUS, rank={}, max_neighbours=1)
    assert stats["queries_truncated"] >= 1
    assert stats["neighbours_dropped"] >= 1


def test_scope_helpers() -> None:
    assert scope_of("@tanstack/react-router") == "@tanstack"
    assert scope_of("react") is None
    assert not same_scope("react", "preact")


def test_pair_is_hashable_ground_truth() -> None:
    pair = SimilarPair("a-name", "b-name", 1)
    assert {pair} == {SimilarPair("a-name", "b-name", 1)}
