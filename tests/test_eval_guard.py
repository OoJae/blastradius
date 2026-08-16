"""The guard must refuse the shortcuts, and refuse them before the database.

These run offline in the default suite, because the guard is what makes the
evaluation's discovery claims mean anything. If it silently stopped working,
every recall number would quietly become circular.
"""

from __future__ import annotations

import pytest

from eval.guard import (
    ForbiddenQuery,
    GuardedSession,
    Policy,
    PolicyError,
    discovery_policy,
    product_policy,
)

VICTIMS = ("@uipath/uipath-mcp", "@squawk/airports", "@mistralai/mistralai")

# The obvious cheat: walk from the advisory to the packages it names. It is
# kept here in the open so a reviewer can see precisely which shortcut was
# ruled out, rather than taking the guard's word for it.
PLANTED_CHEAT = (
    "MATCH (a:Advisory)-[:AFFECTS]->(v:Version)-[:VERSION_OF]->(p:Package) "
    "RETURN DISTINCT p.name AS name"
)

# The real shapes the evaluation issues; all must pass discovery.
DISCOVERY_SHAPES = (
    "MATCH (c:Package {id: $id})-[:PKG_DEPENDED_BY*1..3]->(d:Package) RETURN DISTINCT d.name AS name",
    "MATCH (m:Maintainer)-[:MAINTAINS]->(p:Package {id: $id}) RETURN m.key AS key",
    "MATCH (m:Maintainer {id: $id})-[:MAINTAINS]->(q:Package) RETURN q.name AS name",
    "MATCH (p:Package {id: $id})-[s:SIMILAR_NAME]->(q:Package) RETURN q.name AS name, s.distance AS distance",
    "CALL algo.MSpaths({sourceLabel: 'Package', sourceProperty: 'key', "
    "sourceValues: ['pkg:npm/x'], relTypes: ['PKG_DEPENDS_ON'], relDirection: 'incoming', "
    "maxLen: 3, pathCount: $pc, resultLimit: $rl}) YIELD path RETURN path",
)


class StubSession:
    """Records calls so a test can prove the driver was never reached."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def run(self, query: str, **params):
        self.calls.append(query)
        return []


def test_planted_cheat_is_refused() -> None:
    with pytest.raises(ForbiddenQuery) as excinfo:
        discovery_policy(VICTIMS).check(PLANTED_CHEAT)
    assert excinfo.value.rule == "denied_token"
    assert excinfo.value.matched == "AFFECTS"


def test_guard_fires_before_the_driver_is_touched() -> None:
    stub = StubSession()
    session = GuardedSession(stub, discovery_policy(VICTIMS))
    with pytest.raises(ForbiddenQuery):
        session.run(PLANTED_CHEAT)
    assert stub.calls == [], "the query reached the database despite being refused"
    assert session.queries == []


@pytest.mark.parametrize("policy_factory", [discovery_policy, product_policy])
def test_victim_name_literal_is_refused_under_every_policy(policy_factory) -> None:
    # Even the product policy, which may read the advisory, must never be handed
    # the answer key directly.
    query = "MATCH (p:Package) WHERE p.name STARTS WITH '@uipath/uipath-mcp' RETURN p.name AS name"
    with pytest.raises(ForbiddenQuery) as excinfo:
        policy_factory(VICTIMS).check(query)
    assert excinfo.value.rule == "answer_key_literal"


def test_compromised_flag_is_refused_for_discovery() -> None:
    query = "MATCH (v:Version) WHERE v.compromised = true RETURN v.name AS name"
    with pytest.raises(ForbiddenQuery) as excinfo:
        discovery_policy(VICTIMS).check(query)
    assert excinfo.value.matched == "compromised"


def test_unknown_relationship_type_is_refused() -> None:
    # Fails closed: a relationship the policy has never heard of is refused
    # rather than allowed by omission.
    query = "MATCH (a:Package)-[:PKG_SECRETLY_KNOWS]->(b:Package) RETURN b.name AS name"
    with pytest.raises(ForbiddenQuery) as excinfo:
        discovery_policy(VICTIMS).check(query)
    assert excinfo.value.rule == "relationship_type"


def test_path_procedure_options_are_inspected() -> None:
    # A procedure call carries its relationship types in an option map rather
    # than a bracket pattern, so a guard that only reads `[:TYPE]` would miss it.
    query = (
        "CALL algo.MSpaths({sourceLabel: 'Package', sourceProperty: 'key', "
        "sourceValues: ['pkg:npm/x'], relTypes: ['AFFECTS'], relDirection: 'incoming', "
        "maxLen: 3, pathCount: 1, resultLimit: 10}) YIELD path RETURN path"
    )
    with pytest.raises(ForbiddenQuery):
        discovery_policy(VICTIMS).check(query)


def test_advisory_label_is_refused_for_discovery() -> None:
    query = "MATCH (a:Advisory) RETURN count(*) AS n"
    with pytest.raises(ForbiddenQuery):
        discovery_policy(VICTIMS).check(query)


@pytest.mark.parametrize("query", DISCOVERY_SHAPES)
def test_real_discovery_queries_pass(query: str) -> None:
    discovery_policy(VICTIMS).check(query)


def test_product_policy_may_read_the_advisory() -> None:
    # Answering "is this pinned version malicious" from the advisory is the
    # advisory doing its job, not a shortcut.
    query = (
        "MATCH (a:Advisory)-[r:AFFECTS]->(v:Version {id: $id}) "
        "RETURN a.key AS advisory, r.live_from AS live_from, r.live_until AS live_until"
    )
    product_policy(VICTIMS).check(query)


def test_short_literals_force_explicit_review() -> None:
    with pytest.raises(PolicyError):
        Policy(
            name="x",
            allowed_rel_types=frozenset(),
            allowed_labels=frozenset(),
            denied_tokens=frozenset(),
            denied_literals=frozenset({"ab"}),
        )


def test_permitted_queries_are_transcribed() -> None:
    stub = StubSession()
    session = GuardedSession(stub, discovery_policy(VICTIMS))
    session.run(DISCOVERY_SHAPES[0], id=1)
    assert stub.calls == [DISCOVERY_SHAPES[0]]
    assert session.queries == [DISCOVERY_SHAPES[0]]
