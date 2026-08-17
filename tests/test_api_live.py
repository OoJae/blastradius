"""The API against a real graph.

Assertions are written as relationships and invariants rather than fixed totals,
because two different graphs are legitimate targets: the 30-node fixture graph a
stranger gets from `just ingest-demo`, and the 52k-package demo slice. A test
that only passes on one of them would fail the fresh-clone gate.

Two of these carry more weight than the rest. `?fresh=1` proving a query really
re-ran, and cached autocomplete agreeing with the live graph query, are what
stop "HydraDB answered this" from quietly meaning "memory answered this".
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def any_package(client) -> str:
    """A package this graph actually holds, whichever graph is loaded."""
    matches = client.get("/api/suggest", params={"q": "a", "limit": 1}).json()["result"]["matches"]
    if not matches:
        matches = client.get("/api/suggest", params={"q": "f", "limit": 1}).json()["result"]["matches"]
    assert matches, "the graph appears to hold no packages"
    return matches[0]["name"]


def test_health_reports_ready(client) -> None:
    body = client.get("/api/health").json()
    assert body["ready"] is True


def test_stats_come_from_the_graph(client) -> None:
    result = client.get("/api/stats").json()["result"]
    assert result["graph"]["package"]["value"] > 0
    assert result["graph"]["package"]["source"] == "hydra"
    # The boot queries are shown, so the cached numbers can be traced back to
    # the statements that produced them.
    assert result["boot_queries"], "no boot queries recorded"
    assert any("MATCH (p:Package)" in step["cypher"] for step in result["boot_queries"])


def test_every_endpoint_reports_the_query_it_ran(client, any_package) -> None:
    for path, params in (
        ("/api/blast-radius", {"pkg": any_package, "depth": 1, "limit": 5}),
        (f"/api/maintainer-overlap/{any_package}", {}),
        (f"/api/typosquats/{any_package}", {}),
    ):
        body = client.get(path, params=params).json()
        assert body["status"] == "ok", path
        assert body["hydra"]["cypher"].strip(), f"{path} reported no statement"
        assert body["hydra"]["ms"] >= 0
        assert body["hydra"]["steps"], f"{path} recorded no steps"


def test_fresh_forces_a_live_query(client, any_package) -> None:
    """The anti-cache-theatre check.

    A cached answer and a computed one must be distinguishable, or the claim
    that HydraDB produced a number is unverifiable.
    """
    params = {"pkg": any_package, "depth": 1, "limit": 1}
    client.get("/api/blast-radius", params=params)  # warm it

    cached = client.get("/api/blast-radius", params=params).json()
    fresh = client.get("/api/blast-radius", params={**params, "fresh": "true"}).json()

    assert any(step["cached"] for step in cached["hydra"]["steps"]), "nothing was cached"
    radius_steps = [s for s in fresh["hydra"]["steps"] if s["name"] == "q1_radius_nodes"]
    assert radius_steps and not radius_steps[0]["cached"], "fresh=1 did not re-run the traversal"
    assert cached["result"]["total"] == fresh["result"]["total"], "cache disagreed with the graph"


def test_cached_autocomplete_agrees_with_the_graph(client, any_package) -> None:
    """The other honesty check: the index is a cache, not a second engine.

    Compared on a prefix narrow enough to return fewer than a page of matches.
    On a broad prefix both answers are correct but different: the live query
    has no ORDER BY, so its ten are whichever ten the scan reached first, while
    the index ranks before truncating. Equality there would be asserting that
    two arbitrary subsets happen to coincide.
    """
    cached = client.get("/api/suggest", params={"q": any_package, "limit": 10}).json()
    live = client.get(
        "/api/suggest", params={"q": any_package, "limit": 10, "live": "true"}
    ).json()

    assert cached["result"]["source"] == "startup index"
    assert live["result"]["source"] == "live"

    cached_names = {m["name"] for m in cached["result"]["matches"]}
    live_names = {m["name"] for m in live["result"]["matches"]}
    if len(live_names) < 10:
        # Not truncated, so the two must name exactly the same packages.
        assert cached_names == live_names, "the startup index disagrees with the graph"
    else:
        assert cached_names & live_names, "the index and the graph share no matches"


def test_the_index_only_returns_packages_the_graph_has(client) -> None:
    """The property that makes the cache defensible: it cannot invent a name."""
    matches = client.get("/api/suggest", params={"q": "a", "limit": 10}).json()["result"]["matches"]
    if not matches:
        pytest.skip("no package in this graph starts with 'a'")

    for match in matches[:5]:
        response = client.get(
            "/api/blast-radius", params={"pkg": match["name"], "depth": 1, "limit": 1}
        )
        assert response.status_code == 200, f"{match['name']} is not in the graph"


def test_radius_grows_with_depth(client, any_package) -> None:
    shallow = client.get(
        "/api/blast-radius", params={"pkg": any_package, "depth": 1, "limit": 20000}
    ).json()["result"]
    deeper = client.get(
        "/api/blast-radius", params={"pkg": any_package, "depth": 2, "limit": 20000}
    ).json()["result"]
    # Depth 2 reaches everything depth 1 reaches, and possibly more.
    assert deeper["total"] >= shallow["total"]
    assert set(d["name"] for d in shallow["dependents"]) <= set(
        d["name"] for d in deeper["dependents"]
    )


def test_an_unknown_package_is_a_graph_answer_not_a_crash(client) -> None:
    response = client.get(
        "/api/blast-radius", params={"pkg": "__definitely-not-published__", "depth": 1}
    )
    assert response.status_code == 404
    body = response.json()
    # The 404 is something the graph told us, so the lookup is in the trace.
    assert body["hydra"]["steps"], "a 404 with no query behind it is a guess"


def test_a_depth_outside_the_whitelist_is_refused(client, any_package) -> None:
    response = client.get("/api/blast-radius", params={"pkg": any_package, "depth": 99})
    assert response.status_code == 422


def test_incident_describes_whichever_advisory_this_graph_holds(client) -> None:
    body = client.get("/api/incident").json()
    if body["status"] == "unavailable":
        pytest.skip("this graph holds no advisory")

    result = body["result"]
    assert result["advisory"]
    assert result["artifacts"] > 0
    assert result["waves"], "publishes were not grouped into bursts"
    for wave in result["waves"]:
        assert wave["versions"] > 0
        assert wave["seconds"] >= 0


def test_exposure_never_shrinks_and_stops_at_saturation(client) -> None:
    body = client.get("/api/incident").json()
    if body["status"] == "unavailable" or not body["result"].get("exposure_curve"):
        pytest.skip("no exposure curve on this graph yet")

    curve = body["result"]["exposure_curve"]
    exposed = [point["exposed"] for point in curve]
    assert exposed == sorted(exposed), "exposure went backwards"

    point = body["result"]["saturation"]
    assert point["exposed"] == max(exposed)
    assert point["offset_seconds"] >= 0


def test_a_lockfile_gets_a_verdict_with_its_reasoning(client) -> None:
    lockfile = {
        "name": "svc-test",
        "lockfileVersion": 3,
        "packages": {
            "": {"name": "svc-test", "version": "1.0.0"},
            "node_modules/__definitely-not-published__": {"version": "1.0.0"},
        },
    }
    body = client.post("/api/lockfile", json=lockfile).json()
    result = body["result"]
    assert result["verdict"] in ("EXPOSED", "UNKNOWN", "AT_RISK", "CLEAN")
    assert result["why"]
    # A package the graph has never heard of must not be called clean.
    entry = result["entries"][0]
    assert entry["verdict"] == "UNKNOWN"
    assert result["coverage"]["note"]


def test_a_v1_lockfile_is_rejected_with_advice(client) -> None:
    response = client.post("/api/lockfile", json={"lockfileVersion": 1, "dependencies": {}})
    assert response.status_code == 422
    assert "npm 7" in response.json()["message"]
