"""The lockfile verdict contract, pinned against the checked-in fixtures.

These run offline because `decide_entry` and `rollup` are pure. The contract
they pin is the one thing in this product that must not drift quietly: a wrong
verdict here tells someone their service is safe when it is not, or panics them
when it is fine.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from api.lockfile import (
    LockEntry,
    LockfileError,
    decide_entry,
    parse_bytes,
    rollup,
)

FIXTURES = Path(__file__).resolve().parent.parent / "data" / "fixtures" / "lockfiles"
EXPECTED = json.loads((FIXTURES / "expected.json").read_text())
ANSWERS = json.loads(
    (FIXTURES.parent / "expected_answers.json").read_text()
)

# The fixture incident: flux-core@1.0.1 and left-pack@4.4.4 are malicious, and
# the blast radius of those packages is what expected_answers.json records.
MALICIOUS = {
    ("flux-core", "1.0.1"),
    ("left-pack", "4.4.4"),
}
LIVE_FROM = 1778527200
LIVE_UNTIL = 1778527560
EXPOSED_NAMES = frozenset(
    name.removeprefix("pkg:npm/")
    for name in ANSWERS["blast_radius"]["pkg:npm/flux-core"]["3"]
)
IN_GRAPH_PACKAGES = EXPOSED_NAMES | {"flux-core", "left-pack", "orm-lite", "standalone", "flux-coree"}
IN_GRAPH_VERSIONS = {
    (row["name"], row["version"])
    for row in [
        {"name": "flux-core", "version": v} for v in ("1.0.0", "1.0.1", "1.0.2")
    ]
    + [{"name": "flux-router", "version": "2.3.0"}, {"name": "left-pack", "version": "4.4.4"}]
}


def verdict_for(name: str, version: str, installed_at: int | None) -> str:
    hit = (
        {"advisory": "adv:GHSA-TEST-0001", "live_from": LIVE_FROM, "live_until": LIVE_UNTIL}
        if (name, version) in MALICIOUS
        else None
    )
    return decide_entry(
        LockEntry(name, version, (f"node_modules/{name}",)),
        advisory_hit=hit,
        package_in_graph=name in IN_GRAPH_PACKAGES,
        version_in_graph=(name, version) in IN_GRAPH_VERSIONS,
        exposed_names=EXPOSED_NAMES,
        exposed_complete=True,
        installed_at=installed_at,
    ).verdict


@pytest.mark.parametrize("case", EXPECTED["lockfiles"], ids=lambda c: c["file"])
def test_every_fixture_entry_verdict_matches(case: dict) -> None:
    for artifact, want in case["entries"].items():
        name, version = artifact.rsplit("@", 1)
        got = verdict_for(name, version, None)
        assert got == want, f"{case['file']}: {artifact} expected {want}, got {got}"


@pytest.mark.parametrize("case", EXPECTED["lockfiles"], ids=lambda c: c["file"])
def test_every_fixture_file_verdict_matches(case: dict) -> None:
    verdicts = []
    for artifact in case["entries"]:
        name, version = artifact.rsplit("@", 1)
        hit = (
            {"advisory": "adv:GHSA-TEST-0001", "live_from": LIVE_FROM, "live_until": LIVE_UNTIL}
            if (name, version) in MALICIOUS
            else None
        )
        verdicts.append(
            decide_entry(
                LockEntry(name, version, (f"node_modules/{name}",)),
                advisory_hit=hit,
                package_in_graph=name in IN_GRAPH_PACKAGES,
                version_in_graph=(name, version) in IN_GRAPH_VERSIONS,
                exposed_names=EXPOSED_NAMES,
                exposed_complete=True,
            )
        )
    assert rollup(verdicts)["verdict"] == case["verdict"], case["file"]


def test_an_at_risk_entry_does_not_by_itself_condemn_the_file() -> None:
    # Two fixtures are CLEAN files containing an AT_RISK entry. The file answers
    # "did this service install something malicious"; the entry answers "what
    # does the graph know about this artifact". Conflating them would fail the
    # contract above, so this states the reason directly.
    at_risk = decide_entry(
        LockEntry("flux-router", "2.3.0", ("node_modules/flux-router",)),
        advisory_hit=None,
        package_in_graph=True,
        version_in_graph=True,
        exposed_names=EXPOSED_NAMES,
        exposed_complete=True,
    )
    assert at_risk.verdict == "AT_RISK"
    summary = rollup([at_risk])
    assert summary["verdict"] == "CLEAN"
    assert summary["counts"]["AT_RISK"] == 1
    assert "blast radius" in summary["why"]


def test_an_incomplete_closure_can_never_yield_clean() -> None:
    # If a traversal was refused the exposed set is a lower bound, so absence
    # from it proves nothing and the honest answer is that we do not know.
    result = decide_entry(
        LockEntry("some-app", "1.0.0", ("node_modules/some-app",)),
        advisory_hit=None,
        package_in_graph=True,
        version_in_graph=True,
        exposed_names=EXPOSED_NAMES,
        exposed_complete=False,
    )
    assert result.verdict == "UNKNOWN"
    assert "could not be computed" in result.reason


def test_an_incomplete_closure_still_reports_a_malicious_version() -> None:
    # A refused traversal must not downgrade positive evidence.
    result = decide_entry(
        LockEntry("flux-core", "1.0.1", ("node_modules/flux-core",)),
        advisory_hit={"advisory": "adv:X", "live_from": LIVE_FROM, "live_until": LIVE_UNTIL},
        package_in_graph=True,
        version_in_graph=True,
        exposed_names=None,
        exposed_complete=False,
    )
    assert result.verdict == "EXPOSED"


def test_a_package_outside_the_slice_is_unknown_not_clean() -> None:
    result = decide_entry(
        LockEntry("never-loaded", "1.0.0", ("node_modules/never-loaded",)),
        advisory_hit=None,
        package_in_graph=False,
        version_in_graph=False,
        exposed_names=EXPOSED_NAMES,
        exposed_complete=True,
    )
    assert result.verdict == "UNKNOWN"


def test_a_missing_version_record_still_permits_a_scoped_clean() -> None:
    # This originally returned UNKNOWN, on the reasoning that without a version
    # record we could not prove the artifact was fine. That conflated two
    # different unknowns. The advisory's manifest is complete -- every artifact
    # it names is loaded -- so whether this entry is one of them is answerable
    # whether or not we store this particular version.
    #
    # It matters because on real data almost nothing has a version record: only
    # the packages the advisory names do. Under the old rule every realistic
    # lockfile came back entirely unprovable, which is not caution, it is a
    # useless answer.
    #
    # CLEAN here is scoped to this advisory, and the reason string says so.
    result = decide_entry(
        LockEntry("orm-lite", "2.0.0", ("node_modules/orm-lite",)),
        advisory_hit=None,
        package_in_graph=True,
        version_in_graph=False,
        exposed_names=EXPOSED_NAMES,
        exposed_complete=True,
    )
    assert result.verdict == "CLEAN"
    assert "this advisory" in result.reason
    # The signal is still reported, so the limit remains visible per entry.
    assert result.signals["version_in_graph"] is False


def test_an_incomplete_closure_still_blocks_clean() -> None:
    # The guard that genuinely matters: if the blast radius did not compute,
    # nothing can be called clean, because AT_RISK cannot be ruled out.
    result = decide_entry(
        LockEntry("orm-lite", "2.0.0", ("node_modules/orm-lite",)),
        advisory_hit=None,
        package_in_graph=True,
        version_in_graph=True,
        exposed_names=EXPOSED_NAMES,
        exposed_complete=False,
    )
    assert result.verdict == "UNKNOWN"


@pytest.mark.parametrize(
    ("installed_at", "window"),
    [
        (LIVE_FROM + 60, "inside"),
        (LIVE_FROM - 86400, "before"),
        (LIVE_UNTIL + 86400, "after"),
        (None, None),
    ],
)
def test_install_time_is_placed_against_the_window(installed_at, window) -> None:
    result = decide_entry(
        LockEntry("flux-core", "1.0.1", ("node_modules/flux-core",)),
        advisory_hit={"advisory": "adv:X", "live_from": LIVE_FROM, "live_until": LIVE_UNTIL},
        package_in_graph=True,
        version_in_graph=True,
        exposed_names=EXPOSED_NAMES,
        exposed_complete=True,
        installed_at=installed_at,
    )
    # Pinning a malicious artifact is exposure regardless of when it happened;
    # the window is reported alongside rather than changing the verdict.
    assert result.verdict == "EXPOSED"
    assert result.window == window


@pytest.mark.parametrize("case", EXPECTED["lockfiles"], ids=lambda c: c["file"])
def test_the_real_fixture_files_parse(case: dict) -> None:
    parsed = parse_bytes((FIXTURES / case["file"]).read_bytes())
    assert parsed.lockfile_version == 3
    resolved = {f"{e.name}@{e.version}" for e in parsed.entries}
    assert set(case["entries"]) <= resolved


def test_a_v1_lockfile_is_refused_with_something_actionable() -> None:
    with pytest.raises(LockfileError) as excinfo:
        parse_bytes(b'{"lockfileVersion": 1, "dependencies": {}}')
    assert excinfo.value.code == "v1_unsupported"
    assert "npm 7" in str(excinfo.value)
