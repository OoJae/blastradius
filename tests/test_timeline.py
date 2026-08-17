"""The incident timeline, checked offline.

The shape these functions describe contradicts the assumption the project
started with -- that the blast radius grew across a six-minute window -- so the
tests below pin the real structure: two short bursts, and an exposed set that
stops growing almost immediately.
"""

from __future__ import annotations

from api.digest import Artifact
from api.timeline import WAVE_GAP_SECONDS, exposure_curve, group_waves, saturation

BASE = 1778527239


def artifact(name: str, version: str, offset: int) -> Artifact:
    return Artifact(
        name=name,
        version=version,
        published_at=BASE + offset,
        live_from=BASE,
        live_until=BASE + 340,
    )


# The real shape: one version per package in a four-second burst, then a second
# version per package five and a half minutes later.
TWO_WAVES = [artifact(f"pkg-{i}", "1.0.0", i % 5) for i in range(42)] + [
    artifact(f"pkg-{i}", "1.0.1", 335 + (i % 5)) for i in range(42)
]


def test_two_bursts_are_separated() -> None:
    waves = group_waves(TWO_WAVES)
    assert len(waves) == 2
    assert waves[0].versions == 42 and waves[1].versions == 42
    assert waves[0].seconds <= 5 and waves[1].seconds <= 5
    # The gap is what makes them distinct bursts rather than one long window.
    assert waves[1].started - waves[0].ended > WAVE_GAP_SECONDS


def test_each_burst_covers_every_package_once() -> None:
    for wave in group_waves(TWO_WAVES):
        assert wave.packages == wave.versions == 42


def test_a_single_burst_is_not_split() -> None:
    waves = group_waves([artifact(f"p{i}", "1.0.0", i) for i in range(10)])
    assert len(waves) == 1


def test_no_artifacts_gives_no_waves() -> None:
    assert group_waves([]) == []
    assert exposure_curve([], {}) == []
    assert saturation([]) is None


def test_exposure_accumulates_as_packages_turn_malicious() -> None:
    artifacts = [artifact("a", "1", 0), artifact("b", "1", 1), artifact("c", "1", 2)]
    closures = {
        "a": frozenset({"x", "y"}),
        "b": frozenset({"y", "z"}),  # overlaps a, so the union must dedupe
        "c": frozenset({"w"}),
    }
    curve = exposure_curve(artifacts, closures)
    assert [point["exposed"] for point in curve] == [2, 3, 4]
    assert [point["packages_compromised"] for point in curve] == [1, 2, 3]


def test_a_second_version_adds_no_new_exposure() -> None:
    # Once a package has any malicious version, its dependents are reachable.
    # Publishing another version of the same package changes nothing.
    artifacts = [artifact("a", "1.0.0", 0), artifact("a", "1.0.1", 335)]
    curve = exposure_curve(artifacts, {"a": frozenset({"x", "y"})})
    assert len(curve) == 1, "keyed on first publish per package, not per version"
    assert curve[0]["exposed"] == 2


def test_saturation_reports_the_first_moment_at_the_peak() -> None:
    artifacts = [artifact("a", "1", 0), artifact("b", "1", 3), artifact("c", "1", 4)]
    closures = {"a": frozenset({"x"}), "b": frozenset({"y"}), "c": frozenset({"y"})}
    point = saturation(exposure_curve(artifacts, closures))
    assert point["exposed"] == 2
    # c adds nothing, so saturation is at b's publish rather than the last one.
    assert point["offset_seconds"] == 3


def test_a_package_with_no_dependents_still_advances_the_count() -> None:
    artifacts = [artifact("a", "1", 0), artifact("lonely", "1", 1)]
    curve = exposure_curve(artifacts, {"a": frozenset({"x"})})
    assert curve[-1]["packages_compromised"] == 2
    assert curve[-1]["exposed"] == 1
