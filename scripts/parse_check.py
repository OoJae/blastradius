"""Execute every query file, because executing is the only way to parse-check.

HydraDB exposes no EXPLAIN over the network, so a statement that this Cypher
subset rejects can only be discovered by sending it. Each file is therefore run
for real -- at every value its whitelist permits -- against a sentinel package
id that exists in no graph. The statement is fully parsed and returns
immediately, so the check is fast, safe against whatever slice is loaded, and
independent of the data.

A parse rejection fails the run. An admission-control refusal on the three
queries that deliberately scan does not: that is a size limit rather than a
syntax error, and parse-check is not the place to assert how big the slice is.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from neo4j.exceptions import Neo4jError

from api.queries import ALLOWED_DEPTHS, COUNTABLE_LABELS, REGISTRY, render, seed_values
from ingest import hydra
from ingest.ids import adv_key, hash_key, pkg_key, ver_key

# A package no graph contains, so anchored queries parse and then match nothing.
SENTINEL = "__blastradius_parse_check_probe__"
SENTINEL_PKG = hash_key(pkg_key(SENTINEL))
SENTINEL_VER = hash_key(ver_key(SENTINEL, "0.0.0"))
SENTINEL_ADV = hash_key(adv_key("GHSA-parse-check-probe"))

# Rejections that mean the statement itself is wrong. These fail the run.
PARSE_MARKERS = (
    "is not supported yet",
    "Invalid input",
    "InvalidSyntax",
    "requires a fixed source id",
    "only supported as an UNWIND input",
    "not executable",
    "matches only id",
)

# Refusals that mean the query was fine but the data was too big for it.
BUDGET_MARKERS = ("admission control", "timeout", "Terminated")

# These deliberately scan, so a budget refusal against a large slice is expected.
SCANNING = {"q8_label_count", "q9_package_roster", "q10_version_roster", "q11_prefix_search"}


def variants(name: str) -> list[tuple[str, dict, dict]]:
    """(label, interpolations, params) for every case a query must survive."""
    if name == "q1_radius_nodes":
        return [
            (f"depth={d}", {"depth": d}, {"seed_id": SENTINEL_PKG})
            for d in ALLOWED_DEPTHS
        ]
    if name == "q8_label_count":
        return [
            (f"label={label}", {"label": label}, {})
            for label in sorted(COUNTABLE_LABELS)
        ]
    if name == "q2_incident_paths":
        return [
            (
                "sentinel seeds",
                {"seed_values": seed_values([SENTINEL, "flux-core"])},
                {"max_len": 2, "path_count": 1, "result_limit": 10},
            )
        ]
    if name == "q1_radius_paths":
        return [("-", {}, {"seed_id": SENTINEL_PKG, "max_len": 2, "path_count": 1, "result_limit": 10})]
    if name in ("q3_advisory_hit",):
        return [("-", {}, {"version_id": SENTINEL_VER})]
    if name == "q3_advisory_hit_in_window":
        return [("-", {}, {"version_id": SENTINEL_VER, "installed_at": 0})]
    if name == "q3_advisory_manifest":
        return [("-", {}, {"advisory_id": SENTINEL_ADV})]
    if name == "q4_maintainers_of":
        return [("-", {}, {"package_id": SENTINEL_PKG})]
    if name == "q4_packages_of_maintainer":
        return [("-", {}, {"maintainer_id": SENTINEL_PKG})]
    if name in ("q5_typosquats", "q6_package_lookup"):
        return [("-", {}, {"package_id": SENTINEL_PKG})]
    if name == "q7_version_lookup":
        return [("-", {}, {"version_id": SENTINEL_VER})]
    if name == "q11_prefix_search":
        return [("-", {}, {"prefix": SENTINEL})]
    return [("-", {}, {})]


def main() -> int:
    failures: list[str] = []
    warnings: list[str] = []

    print(f"{'query':<30} {'variant':<16} {'rows':>6} {'ms':>8}  verdict")
    print("-" * 78)

    with hydra.connect() as driver:
        with hydra.session(driver) as session:
            for name in sorted(REGISTRY):
                for label, interpolations, params in variants(name):
                    cypher = render(name, **interpolations)
                    started = time.perf_counter()
                    try:
                        rows = len(list(session.run(cypher, **params)))
                    except Neo4jError as exc:
                        ms = (time.perf_counter() - started) * 1000
                        text = str(exc)
                        first = text.splitlines()[0][:90]
                        if any(marker in text for marker in PARSE_MARKERS):
                            print(f"{name:<30} {label:<16} {'-':>6} {ms:>8.0f}  FAIL")
                            print(f"    {first}")
                            failures.append(f"{name} [{label}]: {first}")
                        elif any(marker in text for marker in BUDGET_MARKERS) and name in SCANNING:
                            print(f"{name:<30} {label:<16} {'-':>6} {ms:>8.0f}  warn (budget)")
                            warnings.append(f"{name} [{label}]: {first}")
                        else:
                            print(f"{name:<30} {label:<16} {'-':>6} {ms:>8.0f}  FAIL")
                            print(f"    {first}")
                            failures.append(f"{name} [{label}]: {first}")
                        continue
                    ms = (time.perf_counter() - started) * 1000
                    print(f"{name:<30} {label:<16} {rows:>6} {ms:>8.0f}  ok")

    print()
    if warnings:
        print(f"{len(warnings)} budget warning(s) on deliberately-scanning queries:")
        for warning in warnings:
            print(f"  {warning}")
    if failures:
        print(f"\nFAIL: {len(failures)} statement(s) rejected")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print("PASS: every statement parsed and executed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
