"""The query files and the interpolation guard, checked offline.

Two things can only be written into the query text rather than bound as
parameters -- a traversal depth and a path procedure's option lists -- so these
tests exist to keep that from becoming an injection.
"""

from __future__ import annotations

import re

import pytest

from api.queries import (
    ALLOWED_DEPTHS,
    PLACEHOLDER,
    COUNTABLE_LABELS,
    REGISTRY,
    CypherLiteral,
    UnsafeInterpolation,
    render,
    seed_values,
)

# Shapes this Cypher subset rejects. A file containing one would fail at run
# time, and parse-check would catch it -- but catching it offline is cheaper.
BANNED = (
    (r"\bIN\b\s*\[", "IN is not supported"),
    (r"\bCONTAINS\b", "CONTAINS is not supported"),
    (r"\bENDS WITH\b", "ENDS WITH is not supported"),
    (r"\bIS NULL\b", "IS NULL is not supported"),
    (r"count\s*\(\s*DISTINCT", "count(DISTINCT ...) is not executable"),
    (r"RETURN\s+\*", "RETURN * is not executable"),
    (r"\bmin\s*\(", "min() is not available"),
    (r"\bmax\s*\(", "max() is not available"),
)


def statement_of(template: str) -> str:
    """The query text with comment lines removed."""
    return "\n".join(
        line for line in template.splitlines() if not line.strip().startswith("//")
    )


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_one_statement_per_file(name: str) -> None:
    # HydraDB accepts exactly one statement per request.
    body = statement_of(REGISTRY[name].template)
    assert body.count(";") == 0, "a trailing semicolon reads as a second statement"


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_no_unsupported_shapes(name: str) -> None:
    body = statement_of(REGISTRY[name].template)
    for pattern, reason in BANNED:
        assert not re.search(pattern, body), f"{name}: {reason}"


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_placeholder_is_declared(name: str) -> None:
    spec = REGISTRY[name]
    found = set(PLACEHOLDER.findall(spec.template))
    assert found == set(spec.interpolations), (
        f"{name}: placeholders {sorted(found)} but whitelist covers "
        f"{sorted(spec.interpolations)}"
    )


@pytest.mark.parametrize("depth", ALLOWED_DEPTHS)
def test_every_allowed_depth_renders(depth: int) -> None:
    rendered = render("q1_radius_nodes", depth=depth)
    assert f"*1..{depth}]" in rendered
    assert not PLACEHOLDER.search(rendered)


@pytest.mark.parametrize(
    "bad",
    [
        0,
        7,
        99,
        "2",  # right value, wrong type: would still format correctly
        "2]->(x:Package) MATCH (y:Package)-[:PKG_DEPENDED_BY*1..8",
        "1..6",
        None,
        True,  # bool is an int subclass; must not sneak past as depth 1
    ],
)
def test_depth_outside_the_whitelist_is_refused(bad) -> None:
    with pytest.raises(UnsafeInterpolation):
        render("q1_radius_nodes", depth=bad)


def test_missing_and_extra_interpolations_are_refused() -> None:
    with pytest.raises(UnsafeInterpolation):
        render("q1_radius_nodes")
    with pytest.raises(UnsafeInterpolation):
        render("q1_radius_nodes", depth=2, label="Package")


@pytest.mark.parametrize("label", sorted(COUNTABLE_LABELS))
def test_countable_labels_render(label: str) -> None:
    assert f"(n:{label})" in render("q8_label_count", label=label)


def test_uncountable_label_is_refused() -> None:
    # Edge counts are refused by the server; the whitelist stops us asking.
    with pytest.raises(UnsafeInterpolation):
        render("q8_label_count", label="PKG_DEPENDS_ON")


def test_seed_values_must_be_escaped_by_us() -> None:
    # A plain string, however well-formed, is not accepted for an option list.
    with pytest.raises(UnsafeInterpolation):
        render("q2_incident_paths", seed_values="['pkg:npm/react']")


def test_seed_values_escapes_quotes_and_backslashes() -> None:
    literal = seed_values(["ev'il", "back\\slash"])
    assert isinstance(literal, CypherLiteral)

    # Every quote inside the literal is preceded by a backslash, so none of them
    # can close the string and let following characters be read as syntax.
    # (Counting quotes across the whole query proves nothing: the file contains
    # its own quoted literals, and an escaped quote is still a quote character.)
    for position, character in enumerate(literal):
        if character == "'" and position not in (0, len(literal) - 1):
            preceding = literal[:position]
            if preceding.endswith("[") or preceding.endswith(", "):
                continue  # a literal opening its own string
            assert preceding.endswith("\\") or literal[position + 1 :].startswith(
                ("]", ",")
            ), f"unescaped quote at {position} in {literal!r}"

    assert "\\'" in literal and "\\\\" in literal
    rendered = render("q2_incident_paths", seed_values=literal)
    assert "sourceValues: [" in rendered
    assert "DETACH" not in rendered


def test_seed_values_drops_names_the_graph_does_not_have() -> None:
    literal = seed_values(
        ["react", "'; MATCH (n) DETACH DELETE n //"], known={"react", "vue"}
    )
    assert "DETACH" not in literal
    assert "pkg:npm/react" in literal


def test_seed_values_refuses_an_empty_result() -> None:
    with pytest.raises(UnsafeInterpolation):
        seed_values(["never-heard-of-it"], known={"react"})


def test_registry_covers_every_file() -> None:
    # A new .cypher file with an undeclared placeholder should fail loudly here
    # rather than at request time.
    assert len(REGISTRY) >= 11
    for name, spec in REGISTRY.items():
        assert spec.template.strip(), f"{name} is empty"
