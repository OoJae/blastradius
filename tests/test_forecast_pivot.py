"""The forecast's contract, pinned offline.

The interesting properties: the hindsight claim is computed against the
packages that fell *after* the first artifact (never including it), candidates
never include a package that is already compromised, and the remediation
picker chooses the earliest clean release strictly after the window -- or says
plainly that there is none.
"""

from __future__ import annotations

from api.forecast import campaign_boundary, pivot
from api.lockfile import remediation

OWNERS = {
    "first-pkg": frozenset({"mallory-victim"}),
    "second-pkg": frozenset({"mallory-victim"}),
    "third-pkg": frozenset({"mallory-victim", "other-owner"}),
    "unrelated-pkg": frozenset({"someone-else"}),
}
OWNED = {
    "mallory-victim": frozenset({"first-pkg", "second-pkg", "third-pkg", "safe-sibling"}),
    "other-owner": frozenset({"third-pkg", "their-own-thing"}),
    "someone-else": frozenset({"unrelated-pkg"}),
}
ADVISORY = frozenset({"first-pkg", "second-pkg", "third-pkg"})


def test_hindsight_counts_only_what_fell_after_the_first_artifact() -> None:
    facts = pivot("first-pkg", ADVISORY, OWNERS, OWNED)
    assert facts["fell_later"] == 2
    assert facts["flagged"] == 2
    assert facts["missed"] == []


def test_a_victim_owned_by_an_unshared_maintainer_is_reported_missed() -> None:
    owners = dict(OWNERS) | {"second-pkg": frozenset({"someone-else"})}
    owned = dict(OWNED) | {
        "mallory-victim": frozenset({"first-pkg", "third-pkg", "safe-sibling"}),
        "someone-else": frozenset({"second-pkg", "unrelated-pkg"}),
    }
    facts = pivot("first-pkg", ADVISORY, owners, owned)
    assert facts["flagged"] == 1
    assert facts["missed"] == ["second-pkg"]


def test_candidates_exclude_everything_already_compromised() -> None:
    facts = pivot("first-pkg", ADVISORY, OWNERS, OWNED)
    assert set(facts["candidates"]) == {"safe-sibling", "their-own-thing"}
    assert not set(facts["candidates"]) & ADVISORY


def test_the_campaign_boundary_never_counts_the_seeds_as_victims() -> None:
    result = campaign_boundary(["safe-sibling"], ADVISORY)
    assert result["available"]
    # Seeds are excluded from the denominator, so recall is measured against
    # the packages the pivot could not have known about.
    assert result["victims"] > 0
    assert result["flagged"] == 0


def test_remediation_picks_the_earliest_clean_release_after_the_window() -> None:
    rows = [
        {"version": "2.1.0", "published_at": 300},
        {"version": "2.0.0", "published_at": 200},
        {"version": "1.9.9", "published_at": 50},  # predates the window: not a fix
    ]
    result = remediation("pkg", 100, rows)
    assert result["first_clean_version"] == "2.0.0"
    assert result["command"] == "npm install pkg@2.0.0 --ignore-scripts"
    assert "rotate" in result["rotate_credentials"]


def test_remediation_says_plainly_when_no_clean_release_exists() -> None:
    result = remediation("pkg", 100, [{"version": "1.0.0", "published_at": 50}])
    assert result["first_clean_version"] is None
    assert "no clean release" in result["note"]
    assert "rotate" in result["rotate_credentials"]


def test_remediation_degrades_but_still_says_to_rotate_when_refused() -> None:
    result = remediation("pkg", 100, None)
    assert result["first_clean_version"] is None
    assert "rotate" in result["rotate_credentials"]
