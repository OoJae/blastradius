"""The live window must stay derived from evidence, not from memory.

`data/incident/live_window.json` records a window that the demo, the eval and
every lockfile verdict depend on. These tests re-derive it from the checked-in
registry timestamps and assert the recorded values, so editing any of those
files by hand -- or letting the derivation drift -- fails the suite offline,
with no network and no database.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ingest.load import epoch
from ingest.sources.live_window import (
    MAX_SPAN_SECONDS,
    WindowUnderivable,
    derive_window,
    epoch_ceil,
    iso,
    load_pairs,
)

INCIDENT = Path(__file__).resolve().parent.parent / "data" / "incident"

RECORDED = json.loads((INCIDENT / "live_window.json").read_text())
PUBLISH_TIMES = json.loads((INCIDENT / "publish_times.json").read_text())
PAIRS = load_pairs(INCIDENT)


def test_window_rederives_to_the_recorded_values() -> None:
    window = derive_window(PAIRS, PUBLISH_TIMES)
    assert window.live_from == RECORDED["live_from"]
    assert window.live_until == RECORDED["live_until"]
    assert window.argmin == RECORDED["earliest_version"]
    assert window.argmax == RECORDED["latest_version"]


def test_every_malicious_version_is_dated() -> None:
    # Partial evidence would bias the window inward, so the derivation is only
    # trustworthy at full coverage.
    window = derive_window(PAIRS, PUBLISH_TIMES)
    assert window.missing == []
    assert window.pairs_dated == window.pairs_total == 84


def test_recorded_epochs_and_iso_agree() -> None:
    assert iso(RECORDED["live_from"]) == RECORDED["live_from_iso"]
    assert iso(RECORDED["live_until"]) == RECORDED["live_until_iso"]
    assert RECORDED["span_seconds"] == RECORDED["live_until"] - RECORDED["live_from"]


def test_window_is_a_plausible_six_minute_event() -> None:
    span = RECORDED["span_seconds"]
    assert 0 < span <= MAX_SPAN_SECONDS
    assert span < 900, "a six-minute incident should not span more than 15 minutes"


def test_window_covers_every_malicious_publish() -> None:
    # The whole point of the window: no malicious version may fall outside it.
    for name, version in PAIRS:
        stamp = PUBLISH_TIMES[name][version]
        assert RECORDED["live_from"] <= epoch(stamp)
        assert epoch_ceil(stamp) <= RECORDED["live_until"]


def test_upper_bound_rounds_up() -> None:
    # Truncating would place the window's end just before the last publish.
    assert epoch_ceil("2026-05-11T19:26:17.716Z") == epoch("2026-05-11T19:26:17.716Z") + 1
    assert epoch_ceil("2026-05-11T19:26:17Z") == epoch("2026-05-11T19:26:17Z")


def test_partial_evidence_is_refused_not_guessed() -> None:
    sparse = {PAIRS[0][0]: {PAIRS[0][1]: PUBLISH_TIMES[PAIRS[0][0]][PAIRS[0][1]]}}
    with pytest.raises(WindowUnderivable, match="refusing to derive"):
        derive_window(PAIRS, sparse)


def test_absent_timestamps_are_never_invented() -> None:
    with pytest.raises(WindowUnderivable):
        derive_window(PAIRS, {})


def test_derived_window_differs_from_the_advisory_prose() -> None:
    # Recorded so the difference stays visible rather than being smoothed over:
    # the prose says 19:20:00-19:26:00, the registry disagrees at both ends.
    prose_from = epoch("2026-05-11T19:20:00Z")
    prose_until = epoch("2026-05-11T19:26:00Z")
    assert RECORDED["live_from"] != prose_from or RECORDED["live_until"] != prose_until
    # The real window ends later, so the prose would clear a late install.
    assert RECORDED["live_until"] > prose_until
