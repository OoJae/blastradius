"""Write the incident's nodes and edges into a built slice.

The dependency graph comes from deps.dev; everything about the attack itself
comes from OSV and the npm registry. This joins the two: the compromised
versions, the advisory that names them, and the maintainers whose stolen
credentials were the actual propagation vector.

Compromised versions deliberately get no DEPENDS_ON edges. They were
unpublished, so no manifest for them exists anywhere, and inventing one would
be fabricating the very data the product reasons over.
"""

from __future__ import annotations

import csv
import gzip
import json
from pathlib import Path

from ingest.load import epoch
from ingest.sources.npm_registry import fetch_digests_sync

ROOT = Path(__file__).resolve().parents[1]
INCIDENT = ROOT / "data" / "incident"


def _write(path: Path, header: list[str], rows: list[list]) -> int:
    with gzip.open(path, "wt", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return len(rows)


def slice_package_names(slice_dir: Path) -> set[str]:
    names: set[str] = set()
    with gzip.open(slice_dir / "packages.csv.gz", "rt", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            names.add(row["name"])
    return names


def build(slice_dir: Path, *, maintainer_extra: int = 3000, verbose: bool = True) -> dict:
    advisory = json.loads((INCIDENT / "advisory.json").read_text())
    compromised = json.loads((INCIDENT / "compromised_versions.json").read_text())
    window = json.loads((INCIDENT / "live_window.json").read_text())
    publish_times = json.loads((INCIDENT / "publish_times.json").read_text())

    incident_names = sorted({p["name"] for p in compromised["packages"]})
    in_slice = slice_package_names(slice_dir)

    # Versions: every version of an incident package that the registry knows
    # about, flagged where the advisory names it as malicious.
    malicious = {(row["name"], row["version"]) for row in compromised["pairs"]}
    version_rows: list[list] = []
    version_of_rows: list[list] = []
    for name in incident_names:
        for version, stamp in sorted(publish_times.get(name, {}).items()):
            is_bad = (name, version) in malicious
            version_rows.append([name, version, epoch(stamp), int(is_bad)])
            version_of_rows.append([name, version])

    # Advisory -> each concrete malicious version, carrying the derived window.
    affects_rows = [
        [advisory["osv_id"], name, version, window["live_from"], window["live_until"]]
        for name, version in sorted(malicious)
    ]

    advisory_rows = [
        [
            advisory["osv_id"],
            advisory["cve"],
            advisory["severity"],
            advisory["summary"][:500],
            epoch(advisory["published_at"]),
        ]
    ]

    # Maintainers: the incident packages, plus the most-depended-upon packages
    # in the slice so overlap has something to overlap with.
    popular: list[str] = []
    with gzip.open(slice_dir / "packages.csv.gz", "rt", newline="", encoding="utf-8") as handle:
        rows = [(int(r["in_degree"]), r["name"]) for r in csv.DictReader(handle)]
    rows.sort(key=lambda t: (-t[0], t[1]))
    popular = [name for _, name in rows[:maintainer_extra]]

    # The campaign victims need maintainer data too, or the evaluation's
    # maintainer channel cannot even be asked about them -- a package with no
    # MAINTAINS edge is unfindable for a reason that has nothing to do with
    # traversal, and reporting that as a miss would be misleading.
    victims: list[str] = []
    campaign_path = INCIDENT / "campaign.json"
    if campaign_path.exists():
        victims = [
            m["name"]
            for m in json.loads(campaign_path.read_text())["members"]
            if m["ecosystem"] == "npm"
        ]

    crawl = sorted(set(incident_names) | set(popular) | set(victims))
    if verbose:
        print(f"  crawling {len(crawl):,} packages for maintainers...", flush=True)
    digests = fetch_digests_sync(crawl, concurrency=8, progress_every=500)

    maintainer_names: set[str] = set()
    maintains_rows: list[list] = []
    for name, digest in sorted(digests.items()):
        if name not in in_slice and name not in incident_names:
            continue
        for username in digest.maintainers:
            maintainer_names.add(username)
            maintains_rows.append([username, name])

    counts = {
        "versions": _write(
            slice_dir / "versions.csv.gz",
            ["name", "version", "published_at", "compromised"],
            version_rows,
        ),
        "version_of": _write(
            slice_dir / "version_of.csv.gz", ["name", "version"], version_of_rows
        ),
        "advisories": _write(
            slice_dir / "advisories.csv.gz",
            ["key", "cve", "severity", "summary", "published_at"],
            advisory_rows,
        ),
        "affects": _write(
            slice_dir / "affects.csv.gz",
            ["advisory", "name", "version", "live_from", "live_until"],
            affects_rows,
        ),
        "maintainers": _write(
            slice_dir / "maintainers.csv.gz",
            ["username"],
            [[u] for u in sorted(maintainer_names)],
        ),
        "maintains": _write(
            slice_dir / "maintains.csv.gz", ["username", "name"], maintains_rows
        ),
    }

    unreachable = sorted(
        name for name, digest in digests.items() if digest.branch in ("notfound", "tombstoned")
    )
    (slice_dir / "registry_unreachable.json").write_text(
        json.dumps(
            {
                "note": (
                    "Packages the registry does not serve, so no MAINTAINS edge can "
                    "exist for them. Recorded before the evaluation runs so a miss "
                    "here is explained rather than discovered."
                ),
                "count": len(unreachable),
                "packages": unreachable,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    counts["registry_unreachable"] = len(unreachable)
    counts["victims_crawled"] = len(victims)
    counts["incident_packages_in_slice"] = sum(1 for n in incident_names if n in in_slice)
    counts["incident_packages_total"] = len(incident_names)
    counts["maintainer_coverage_packages"] = len(
        {name for _, name in maintains_rows}
    )

    (slice_dir / "incident_manifest.json").write_text(
        json.dumps(counts, indent=2, sort_keys=True) + "\n"
    )
    if verbose:
        for key, value in counts.items():
            print(f"  {key:34s} {value:,}")
    return counts


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Write incident overlay into a slice")
    parser.add_argument("--dir", required=True)
    args = parser.parse_args()
    build(Path(args.dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
