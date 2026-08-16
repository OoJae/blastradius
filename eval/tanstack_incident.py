"""Evaluate BlastRadius against the real Mini Shai-Hulud / TanStack incident.

Two questions, deliberately kept apart because conflating them would flatter
the tool:

1. **Exposure.** Given the compromised packages, can the graph enumerate every
   package that would have installed one? This is what an incident responder
   acts on, it is checked exactly against an offline oracle, and it is the
   headline.

2. **Victim recall.** Of the packages the worm went on to compromise, how many
   does traversal recover? The answer is essentially none, and that is a fact
   about the attack rather than a defect in the graph: the worm travelled
   through stolen CI credentials, not through published dependencies. §4 shows
   the scan that establishes it before showing the number.

Every discovery query runs through a guard that refuses to read the advisory,
so "the traversal found this" cannot quietly mean "the answer key said so".
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from eval import oracle
from eval.guard import GuardedSession, discovery_policy
from ingest import hydra
from ingest.ids import hash_key, pkg_key

ROOT = Path(__file__).resolve().parents[1]
INCIDENT = ROOT / "data" / "incident"
RAW_PACKAGES = ROOT / "data" / "raw" / "depsdev" / "packages"

DEPTHS = (1, 2, 3, 4)

# Codes are tried in this order and the first that applies wins, so each victim
# gets exactly one explanation and the counts reconcile against the roster.
MISS_ORDER = (
    "found",
    "out_of_ecosystem",
    "is_seed",
    "not_in_snapshot",
    "not_in_slice",
    "maintainer_data_missing",
    "no_path_in_slice",
)


@dataclass
class Incident:
    advisory_id: str
    cve: str
    seeds: tuple[str, ...]
    live_from: int
    live_until: int
    window_method: str


@dataclass
class Victim:
    name: str
    ecosystem: str
    osv_id: str


@dataclass
class Findings:
    incident: Incident
    exposure: dict = field(default_factory=dict)
    recall: dict = field(default_factory=dict)
    taxonomy: dict = field(default_factory=dict)
    sanity: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)


def load_incident() -> Incident:
    advisory = json.loads((INCIDENT / "advisory.json").read_text())
    compromised = json.loads((INCIDENT / "compromised_versions.json").read_text())
    window = json.loads((INCIDENT / "live_window.json").read_text())
    return Incident(
        advisory_id=advisory["osv_id"],
        cve=advisory["cve"],
        seeds=tuple(sorted(p["name"] for p in compromised["packages"])),
        live_from=window["live_from"],
        live_until=window["live_until"],
        window_method=window["method"],
    )


def load_victims() -> list[Victim]:
    path = INCIDENT / "campaign.json"
    if not path.exists():
        return []
    return [
        Victim(m["name"], m["ecosystem"], m["osv_id"])
        for m in json.loads(path.read_text())["members"]
    ]


def graph_closure(session, name: str, depth: int) -> tuple[set[str], float]:
    """Anchored reverse traversal. Depth is a literal: bounds cannot be bound."""
    started = time.perf_counter()
    records = session.run(
        f"MATCH (c:Package {{id: $id}})-[:PKG_DEPENDED_BY*1..{depth}]->(d:Package) "
        "RETURN DISTINCT d.name AS name",
        id=hash_key(pkg_key(name)),
    )
    names = {record["name"] for record in records}
    return names, (time.perf_counter() - started) * 1000


def measure_exposure(
    session, incident: Incident, reverse, depths: Sequence[int] = DEPTHS
) -> dict:
    """Union closure over every compromised package, graph vs offline oracle."""
    results = {}
    for depth in depths:
        oracle_depths = oracle.union_min_depth(reverse, list(incident.seeds), depth)
        want = oracle.closure_at(oracle_depths, depth)

        found: set[str] = set()
        latencies: list[float] = []
        error: str | None = None
        started = time.perf_counter()
        for seed in incident.seeds:
            try:
                names, ms = graph_closure(session, seed, depth)
            except Exception as exc:  # noqa: BLE001 - server-side refusal
                # A refused traversal is recorded as unmeasured, never as a
                # smaller number: a truncated answer that looks plausible is
                # the failure mode this whole evaluation exists to avoid.
                error = str(exc).splitlines()[0][:200]
                break
            found |= names
            latencies.append(ms)
        wall = time.perf_counter() - started

        latencies.sort()
        results[depth] = {
            "graph": None if error else len(found),
            "oracle": len(want),
            "exact": None if error else found == want,
            "missing_from_graph": None if error else len(want - found),
            "extra_in_graph": None if error else len(found - want),
            "wall_seconds": round(wall, 1),
            "p50_ms": round(latencies[len(latencies) // 2], 1) if latencies else None,
            "max_ms": round(latencies[-1], 1) if latencies else None,
            "error": error,
            "_set": frozenset() if error else frozenset(found),
        }
    return results


def measure_recall(
    session,
    incident: Incident,
    victims: list[Victim],
    exposure: dict,
    slice_names: frozenset[str],
) -> dict:
    """How many campaign victims each discovery channel recovers."""
    seeds = set(incident.seeds)
    npm_victims = [v for v in victims if v.ecosystem.lower() == "npm"]
    in_slice = [v for v in npm_victims if v.name in slice_names]
    # A seed cannot be a discovery: it is the input.
    scorable = [v for v in in_slice if v.name not in seeds]
    scorable_names = {v.name for v in scorable}

    deepest = max(d for d in exposure if exposure[d]["graph"] is not None)
    dependency_hits = scorable_names & exposure[deepest]["_set"]

    # Maintainer overlap: two single-hop queries per seed, as the Cypher subset
    # rejects a variable-length pattern even at depth 1.
    maintainers: set[str] = set()
    for seed in incident.seeds:
        for record in session.run(
            "MATCH (m:Maintainer)-[:MAINTAINS]->(p:Package {id: $id}) RETURN m.key AS key",
            id=hash_key(pkg_key(seed)),
        ):
            maintainers.add(record["key"])

    maintainer_reach: set[str] = set()
    for key in maintainers:
        for record in session.run(
            "MATCH (m:Maintainer {id: $id})-[:MAINTAINS]->(q:Package) RETURN q.name AS name",
            id=hash_key(key),
        ):
            maintainer_reach.add(record["name"])
    maintainer_hits = scorable_names & maintainer_reach

    # Typosquat proximity. Query every seed first, then decide whether the
    # channel was measurable: a single seed having no neighbours says nothing
    # about whether the edge type was loaded, and treating that as "no edges"
    # would report a missing dataset as a score of zero.
    similar_reach: set[str] = set()
    seeds_with_neighbours = 0
    for seed in incident.seeds:
        names = {
            record["name"]
            for record in session.run(
                "MATCH (p:Package {id: $id})-[:SIMILAR_NAME]->(q:Package) RETURN q.name AS name",
                id=hash_key(pkg_key(seed)),
            )
        }
        if names:
            seeds_with_neighbours += 1
        similar_reach |= names
    # Whether the edge type was loaded at all is a different question from
    # whether it reaches these seeds, and conflating them would report a
    # missing dataset as a measured zero.
    edges_loaded = bool(
        list(
            session.run(
                "MATCH (a:Package)-[r:SIMILAR_NAME]->(b:Package) RETURN b.name AS name LIMIT 1"
            )
        )
    )
    similar_hits = scorable_names & similar_reach

    union = dependency_hits | maintainer_hits | similar_hits

    def channel(name, hits, searched, status="measured"):
        return {
            "channel": name,
            "found": len(hits),
            "denominator": len(scorable),
            "recall": round(len(hits) / len(scorable), 4) if scorable else None,
            "searched": searched,
            "status": status,
            "hits": sorted(hits),
        }

    return {
        "denominators": {
            "campaign_records": len(victims),
            "npm_victims": len(npm_victims),
            "victims_in_slice": len(in_slice),
            "scorable": len(scorable),
        },
        "channels": [
            channel(f"dependency closure (depth {deepest})", dependency_hits, len(exposure[deepest]["_set"])),
            channel("maintainer overlap", maintainer_hits, len(maintainer_reach)),
            channel(
                "name similarity",
                similar_hits,
                len(similar_reach),
                "measured"
                if edges_loaded
                else "not measured (no SIMILAR_NAME edges loaded)",
            ),
            channel("union of all channels", union, None),
        ],
        "similar_name": {
            "edges_loaded": edges_loaded,
            "seeds_with_neighbours": seeds_with_neighbours,
            "note": (
                "Every compromised package is under @tanstack, and the names "
                "closest to them are @tanstack siblings, which same-scope "
                "suppression removes as owner-published rather than squatted. "
                "So the channel is measurable and genuinely finds nothing here."
            ),
        },
        "_scorable": scorable,
        "_found": union,
        "_deepest": deepest,
    }


def classify(
    victims: list[Victim],
    incident: Incident,
    recall: dict,
    slice_names: frozenset[str],
    snapshot_names: frozenset[str],
    unreachable: frozenset[str],
) -> dict:
    """One explanation per victim, so every miss has a stated reason."""
    seeds = set(incident.seeds)
    found = recall["_found"]
    counts: dict[str, list[str]] = {code: [] for code in MISS_ORDER}

    for victim in victims:
        name = victim.name
        if name in found:
            code = "found"
        elif victim.ecosystem.lower() != "npm":
            code = "out_of_ecosystem"
        elif name in seeds:
            code = "is_seed"
        elif name not in snapshot_names:
            code = "not_in_snapshot"
        elif name not in slice_names:
            code = "not_in_slice"
        elif name in unreachable:
            code = "maintainer_data_missing"
        else:
            code = "no_path_in_slice"
        counts[code].append(name)

    total = sum(len(names) for names in counts.values())
    assert total == len(victims), f"taxonomy lost victims: {total} != {len(victims)}"
    return counts


def git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def run(slice_dir: Path, depths: Sequence[int] = DEPTHS) -> Findings:
    incident = load_incident()
    victims = load_victims()
    slice_names = oracle.slice_roster(slice_dir)
    unreachable = oracle.registry_unreachable(slice_dir)

    print("  building offline reverse index...", flush=True)
    reverse = oracle.build_reverse_index(slice_dir)

    transcript = ROOT / "data" / "eval" / "queries.jsonl"
    if transcript.exists():
        transcript.unlink()

    with hydra.connect() as driver:
        with hydra.session(driver) as raw_session:
            # Every discovery query is checked before it reaches the driver.
            session = GuardedSession(
                raw_session,
                discovery_policy(v.name for v in victims),
                transcript=transcript,
            )
            print("  measuring exposure...", flush=True)
            exposure = measure_exposure(session, incident, reverse, depths)
            print("  measuring recall channels...", flush=True)
            recall = measure_recall(session, incident, victims, exposure, slice_names)
            queries_run = len(session.queries)

    print("  reading snapshot roster...", flush=True)
    snapshot_names = oracle.snapshot_roster(RAW_PACKAGES)
    taxonomy = classify(victims, incident, recall, slice_names, snapshot_names, unreachable)

    return Findings(
        incident=incident,
        exposure=exposure,
        recall=recall,
        taxonomy=taxonomy,
        sanity=json.loads((INCIDENT / "campaign.json").read_text())["counts"],
        meta={
            "slice": slice_dir.name,
            "slice_packages": len(slice_names),
            "snapshot_packages": len(snapshot_names),
            "queries_run": queries_run,
            "guard_policy": "discovery",
            "git_sha": git_sha(),
        },
    )
