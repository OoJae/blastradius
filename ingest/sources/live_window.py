"""Derive the incident's live window from real npm publish timestamps.

The advisory states the window only in prose, inside a markdown table in its
`details` field. Rather than parse that -- or hardcode it -- the window is
computed from the registry's own timestamps for the 84 malicious versions.

This matters beyond tidiness. The prose says 19:20 to 19:26; the registry says
the last malicious version landed at 19:26:17.716. A lockfile installed at
19:26:10 is clean under the prose and exposed under the data. The derived
window is the one that answers the question the product actually asks.

Three things keep it from drifting: the derivation is recorded in full, an
offline test re-derives it and asserts the recorded values, and verify_counts
checks that every AFFECTS edge in the graph carries exactly these epochs.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ingest.load import epoch

# A version whose timestamp we cannot find is excluded from the window and
# listed, never guessed at. If too many are missing the derivation is refused
# outright rather than quietly computed from a fraction of the evidence.
MIN_COVERAGE = 0.90

# The event lasted six minutes. Anything approaching an hour means we have
# picked up an unrelated timestamp and the result should not be trusted.
MAX_SPAN_SECONDS = 3600


class WindowUnderivable(RuntimeError):
    """Raised rather than falling back to a guessed or quoted window."""


@dataclass
class WindowRecord:
    live_from: int
    live_until: int
    method: str
    coverage: float
    pairs_total: int
    pairs_dated: int
    argmin: str
    argmax: str
    missing: list[str] = field(default_factory=list)

    @property
    def span_seconds(self) -> int:
        return self.live_until - self.live_from

    def as_dict(self) -> dict:
        return {
            "live_from": self.live_from,
            "live_until": self.live_until,
            "live_from_iso": iso(self.live_from),
            "live_until_iso": iso(self.live_until),
            "span_seconds": self.span_seconds,
            "method": self.method,
            "coverage": round(self.coverage, 4),
            "pairs_total": self.pairs_total,
            "pairs_dated": self.pairs_dated,
            "earliest_version": self.argmin,
            "latest_version": self.argmax,
            "missing": self.missing,
            "note": (
                "Derived from npm registry publish timestamps for the malicious "
                "versions, not from the advisory prose. Both are reported in the "
                "eval so the difference is visible."
            ),
        }


def iso(epoch_seconds: int) -> str:
    return (
        datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def epoch_ceil(iso_timestamp: str) -> int:
    """Epoch seconds, rounded up.

    `epoch()` truncates, which would place the window's end a fraction of a
    second before the last malicious publish. The window must never be narrower
    than the truth, so the upper bound rounds up.
    """
    text = iso_timestamp.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return math.ceil(parsed.timestamp())


def derive_window(
    pairs: list[tuple[str, str]],
    publish_times: dict[str, dict[str, str]],
    *,
    method: str = "npm_registry_time",
) -> WindowRecord:
    """Compute the window from (package, version) pairs and their timestamps.

    `publish_times` maps package name -> {version -> ISO timestamp}.
    """
    dated: list[tuple[str, str, str]] = []
    missing: list[str] = []

    for name, version in pairs:
        stamp = publish_times.get(name, {}).get(version)
        if stamp:
            dated.append((name, version, stamp))
        else:
            missing.append(f"{name}@{version}")

    if not dated:
        raise WindowUnderivable("no publish timestamps found for any malicious version")

    coverage = len(dated) / len(pairs)
    if coverage < MIN_COVERAGE:
        raise WindowUnderivable(
            f"only {len(dated)}/{len(pairs)} versions have timestamps "
            f"({coverage:.0%} < {MIN_COVERAGE:.0%}); refusing to derive a window "
            f"from partial evidence. Missing: {missing[:5]}"
        )

    lowest = min(dated, key=lambda row: epoch(row[2]))
    highest = max(dated, key=lambda row: epoch_ceil(row[2]))

    live_from = epoch(lowest[2])
    live_until = epoch_ceil(highest[2])

    if live_until <= live_from:
        raise WindowUnderivable(f"window is not positive: {live_from}..{live_until}")
    if live_until - live_from > MAX_SPAN_SECONDS:
        raise WindowUnderivable(
            f"window spans {live_until - live_from}s, over the {MAX_SPAN_SECONDS}s "
            "sanity bound; a timestamp is probably from a different event"
        )

    return WindowRecord(
        live_from=live_from,
        live_until=live_until,
        method=method,
        coverage=coverage,
        pairs_total=len(pairs),
        pairs_dated=len(dated),
        argmin=f"{lowest[0]}@{lowest[1]}",
        argmax=f"{highest[0]}@{highest[1]}",
        missing=sorted(missing),
    )


def load_pairs(incident_dir: Path) -> list[tuple[str, str]]:
    payload = json.loads((incident_dir / "compromised_versions.json").read_text())
    return [(row["name"], row["version"]) for row in payload["pairs"]]
