"""The attacker's remaining reach, from the maintainer pivot.

The worm did not spread through dependencies: it published with stolen
maintainer credentials. So the moment one package is known compromised, the
operational question is not only "who installs it" but "where else can those
credentials publish". That set is one heterogeneous traversal away --
package <- maintainer -> package -- and it is checkable against this very
incident: seeded with nothing but the *first* artifact, the pivot should flag
the packages that actually fell in the minutes after it.

Two claims are kept strictly apart, because conflating them would flatter the
tool. The pivot maps the attacker's *current token reach*, and this incident
confirms it (the number is measured here, not assumed). It does *not* predict
who gets phished next: the broader campaign's victims share no maintainer with
these seeds, docs/EVAL_REPORT.md measures that recall at zero, and the payload
says so instead of hiding it.

`pivot()` is pure -- rows in, facts out -- so the contract is pinned by an
offline test. `build()` does the querying, once, at boot.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from api.closure import ClosureCache
from api.digest import GraphDigest
from api.hydra import HydraClient, NotComputed
from api.trace import QueryRun, Trace
from ingest.ids import hash_key, pkg_key
from ingest.sources.live_window import iso

CAMPAIGN = Path(__file__).resolve().parents[1] / "data" / "incident" / "campaign.json"


@dataclass(frozen=True)
class Forecast:
    payload: dict[str, Any]
    steps: tuple[QueryRun, ...]
    computed_at: float


def pivot(
    first_package: str,
    advisory_packages: frozenset[str],
    owners_by_package: Mapping[str, frozenset[str]],
    owned_by_maintainer: Mapping[str, frozenset[str]],
) -> dict[str, Any]:
    """What the maintainer edges say, given who owns what.

    `hindsight` is the checkable claim: seeded with only the first artifact's
    package, how many of the packages that fell *afterwards* were already one
    maintainer hop away? `candidates` is the actionable claim: every package
    the compromised credentials can still publish to.
    """
    first_owners = owners_by_package.get(first_package, frozenset())
    first_reach = frozenset().union(
        *(owned_by_maintainer.get(m, frozenset()) for m in first_owners)
    ) - {first_package}

    fell_later = advisory_packages - {first_package}
    flagged = fell_later & first_reach

    all_owners = frozenset().union(
        *(owners_by_package.get(p, frozenset()) for p in advisory_packages)
    )
    reach = frozenset().union(
        *(owned_by_maintainer.get(m, frozenset()) for m in all_owners)
    )
    candidates = reach - advisory_packages

    return {
        "first_owners": sorted(first_owners),
        "all_owners": sorted(all_owners),
        "fell_later": len(fell_later),
        "flagged": len(flagged),
        "missed": sorted(fell_later - first_reach),
        "candidates": sorted(candidates),
    }


def campaign_boundary(candidates: Sequence[str], advisory_packages: frozenset[str]) -> dict[str, Any]:
    """How many of the broader campaign's victims this pivot would have found.

    The honest answer is approximately none, and it is reported as a boundary
    rather than buried: the campaign chose its victims by phishing, which no
    edge in this graph represents.
    """
    try:
        roster = json.loads(CAMPAIGN.read_text())
    except OSError:
        return {"available": False}
    victims = sorted(
        {
            m["name"]
            for m in roster.get("members", [])
            if m.get("ecosystem") == "npm" and m["name"] not in advisory_packages
        }
    )
    flagged = sorted(set(victims) & set(candidates))
    return {
        "available": True,
        "victims": len(victims),
        "flagged": len(flagged),
        "note": (
            "the broader campaign spread through phished maintainers who share "
            "nothing with these seeds -- this forecast maps the attacker's current "
            "token reach, it does not predict who gets phished next; the recall "
            "measurement is in docs/EVAL_REPORT.md"
        ),
    }


async def build(
    client: HydraClient,
    digest: GraphDigest,
    closures: ClosureCache,
    depth: int,
) -> Forecast | None:
    """Query the pivot and the candidate radii, once, at boot."""
    if not digest.artifacts:
        return None
    trace = Trace()
    built_at = time.time()

    first = min(digest.artifacts.values(), key=lambda a: a.published_at)
    advisory_packages = frozenset(digest.incident_packages)

    owners_by_package: dict[str, frozenset[str]] = {}
    owned_by_maintainer: dict[str, frozenset[str]] = {}
    for package in sorted(advisory_packages):
        rows = await client.run_spec(
            "q4_maintainers_of", trace=trace, package_id=hash_key(pkg_key(package))
        )
        owners_by_package[package] = frozenset(row["username"] for row in rows)
        for row in rows:
            if row["username"] in owned_by_maintainer:
                continue
            owned = await client.run_spec(
                "q4_packages_of_maintainer",
                trace=trace,
                maintainer_id=hash_key(row["key"]),
            )
            owned_by_maintainer[row["username"]] = frozenset(o["name"] for o in owned)

    facts = pivot(first.name, advisory_packages, owners_by_package, owned_by_maintainer)

    # Every candidate's own blast radius, so the list ranks by consequence
    # rather than alphabet. Radii land in the shared closure cache, so a
    # candidate someone then searches for is answered from this same work.
    candidates: list[dict[str, Any]] = []
    union: set[str] = set()
    refused: list[str] = []
    for name in facts["candidates"]:
        try:
            closure = await closures.radius(name, depth, trace=trace)
        except NotComputed:
            refused.append(name)
            candidates.append({"name": name, "is_popular": name in digest.popular,
                               "radius_total": None, "radius_depth": depth})
            continue
        union |= closure.names
        candidates.append({"name": name, "is_popular": name in digest.popular,
                           "radius_total": len(closure.names), "radius_depth": depth})
    candidates.sort(key=lambda c: (-(c["radius_total"] or -1), c["name"]))

    exposed = closures._exposed.get(depth)
    already = exposed.names if exposed is not None else frozenset()
    net_new = union - already - advisory_packages

    payload = {
        "advisory": digest.advisory_key,
        "seeds": {
            "packages": len(advisory_packages),
            "first_artifact": {
                "name": first.name,
                "version": first.version,
                "published_at": first.published_at,
                "published_at_iso": iso(first.published_at),
            },
        },
        "maintainers": [
            {"username": m, "packages_owned": len(owned_by_maintainer.get(m, ()))}
            for m in facts["all_owners"]
        ],
        "hindsight": {
            "known_then": 1,
            "fell_later": facts["fell_later"],
            "flagged": facts["flagged"],
            "missed": facts["missed"],
            "note": (
                "seeded with only the first artifact, before the rest fell: how many "
                "of the packages compromised in the following minutes were already "
                "one maintainer hop away"
            ),
        },
        "candidates": candidates,
        "reach": {
            "packages": len(facts["candidates"]),
            "union_radius": len(union),
            "net_new": len(net_new),
            "depth": depth,
            "refused": refused,
            "complete": not refused and (exposed.complete if exposed else False),
        },
        "boundary": campaign_boundary(facts["candidates"], advisory_packages),
        "computed_at": built_at,
    }

    steps = tuple(
        run if run.computed_at is not None else QueryRun(
            name=run.name, cypher=run.cypher, params=run.params,
            ms=run.ms, rows=run.rows, cached=run.cached, computed_at=built_at,
        )
        for run in trace.steps
    )
    return Forecast(payload=payload, steps=steps, computed_at=built_at)
