"""Incident ground truth from OSV and the OpenSSF malicious-packages repository.

Produces the small, reviewable set of facts under `data/incident/` that the
loader and the evaluation both build on. Those files are checked into git on
purpose: they are the evidence for every number the product claims, and they
must stay reproducible even if upstream data moves.

Two shapes of the real data drive the design here, both verified against live
OSV rather than assumed:

* `affected[]` is version-major. The umbrella advisory has 84 entries for 42
  packages -- one entry per malicious version -- so anything that treats
  `len(affected)` as a package count is wrong.
* `versions[]` is authoritative and `ranges[]` over-matches. One advisory's
  range spans four releases where only one is malicious, and the per-package
  `MAL-*` records carry no ranges at all. Expanding ranges would mark clean
  releases as compromised, which is a false positive in the one place this
  product has to be exact. Ranges are therefore recorded as provenance and
  never expanded.
"""

from __future__ import annotations

import json
import subprocess
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

import httpx

OSV_API = "https://api.osv.dev/v1"
UMBRELLA_ID = "GHSA-g7cv-rxg3-hmpx"

# The campaign has no shared alias: `related` is empty everywhere and each
# per-package MAL record aliases its own distinct GHSA, so alias traversal
# cannot enumerate the victims. What every record in this campaign does share
# is the origin hash of the batch import, and an indicator domain.
CAMPAIGN_ORIGIN_SHA256 = (
    "5e1924464368f0c5816ee84e000cc47017f44045140feafbbc9e685d847ed5a5"
)
CAMPAIGN_IOC_DOMAIN = "git-tanstack.com"

REPO = "ossf/malicious-packages"
INCIDENT_DIR = Path(__file__).resolve().parents[2] / "data" / "incident"
CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache" / "osv"


@dataclass(frozen=True)
class AffectedPackage:
    """One package's malicious versions, collapsed from many `affected[]` entries."""

    ecosystem: str
    name: str
    versions: tuple[str, ...]
    entry_count: int
    ranges_present: bool
    fixed_candidates: tuple[str, ...]


@dataclass
class Advisory:
    osv_id: str
    aliases: tuple[str, ...]
    cve: str
    severity: float
    summary: str
    published_at: str
    affected: list[AffectedPackage] = field(default_factory=list)


def _client(timeout: float = 60.0) -> httpx.Client:
    return httpx.Client(
        timeout=timeout,
        headers={"User-Agent": "BlastRadius/0.1 (Hack Hydra 2026)"},
        follow_redirects=True,
    )


def fetch_vuln(osv_id: str, *, client: httpx.Client | None = None) -> dict[str, Any]:
    """Fetch one advisory, caching the raw response."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = CACHE_DIR / f"{osv_id}.json"
    if cached.exists():
        return json.loads(cached.read_text())

    owned = client is None
    client = client or _client()
    try:
        response = client.get(f"{OSV_API}/vulns/{osv_id}")
        response.raise_for_status()
        document = response.json()
    finally:
        if owned:
            client.close()

    cached.write_text(json.dumps(document, indent=2, sort_keys=True))
    return document


def group_affected(document: dict[str, Any]) -> list[AffectedPackage]:
    """Collapse version-major `affected[]` entries into one record per package.

    Keys on `(ecosystem, name)` rather than the purl: the same package is
    spelled with different escaping in different databases, so purl equality
    silently splits a package in two.
    """
    grouped: dict[tuple[str, str], dict[str, Any]] = {}

    for entry in document.get("affected", []):
        package = entry.get("package", {})
        key = (package.get("ecosystem", ""), package.get("name", ""))
        bucket = grouped.setdefault(
            key, {"versions": set(), "entries": 0, "ranges": False, "fixed": set()}
        )
        bucket["entries"] += 1
        bucket["versions"].update(entry.get("versions", []))

        for range_spec in entry.get("ranges", []):
            bucket["ranges"] = True
            for event in range_spec.get("events", []):
                if "fixed" in event:
                    bucket["fixed"].add(event["fixed"])

    return [
        AffectedPackage(
            ecosystem=ecosystem,
            name=name,
            # Sorted because OSV returns versions unordered.
            versions=tuple(sorted(bucket["versions"])),
            entry_count=bucket["entries"],
            ranges_present=bucket["ranges"],
            fixed_candidates=tuple(sorted(bucket["fixed"])),
        )
        for (ecosystem, name), bucket in sorted(grouped.items())
    ]


def parse_advisory(document: dict[str, Any]) -> Advisory:
    aliases = tuple(document.get("aliases") or ())
    cve = next((alias for alias in aliases if alias.startswith("CVE-")), "")

    severity = 0.0
    database_specific = document.get("database_specific") or {}
    for entry in document.get("severity") or []:
        # CVSS vectors are strings; the numeric score lives in database_specific
        # or must be derived. Prefer an explicit numeric score when present.
        if isinstance(entry.get("score"), (int, float)):
            severity = float(entry["score"])
    if not severity and isinstance(database_specific.get("cvss_score"), (int, float)):
        severity = float(database_specific["cvss_score"])

    return Advisory(
        osv_id=document.get("id", ""),
        aliases=aliases,
        cve=cve,
        severity=severity,
        summary=document.get("summary", ""),
        published_at=document.get("published", ""),
        affected=group_affected(document),
    )


def is_campaign_member(document: dict[str, Any]) -> tuple[bool, list[str]]:
    """Whether an advisory belongs to this campaign, and which markers matched."""
    reasons: list[str] = []

    database_specific = document.get("database_specific") or {}
    for origin in database_specific.get("malicious-packages-origins", []) or []:
        if origin.get("sha256") == CAMPAIGN_ORIGIN_SHA256:
            reasons.append("origin_sha256")
            break

    iocs = database_specific.get("iocs") or {}
    domains = iocs.get("domains") or []
    if CAMPAIGN_IOC_DOMAIN in domains or CAMPAIGN_IOC_DOMAIN in document.get("details", ""):
        reasons.append("ioc_domain")

    return bool(reasons), reasons


def compromised_pairs(advisory: Advisory) -> list[tuple[str, str]]:
    """Every (package, version) the advisory names as malicious."""
    return [
        (package.name, version)
        for package in advisory.affected
        for version in package.versions
    ]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def build_advisory_artifacts(*, out_dir: Path = INCIDENT_DIR) -> Advisory:
    """Fetch the umbrella advisory and write the reviewable ground-truth files."""
    document = fetch_vuln(UMBRELLA_ID)
    advisory = parse_advisory(document)
    pairs = compromised_pairs(advisory)

    write_json(
        out_dir / "advisory.json",
        {
            "osv_id": advisory.osv_id,
            "aliases": list(advisory.aliases),
            "cve": advisory.cve,
            "severity": advisory.severity,
            "summary": advisory.summary,
            "published_at": advisory.published_at,
            "affected_entries": len(document.get("affected", [])),
            "affected_packages": len(advisory.affected),
            "compromised_versions": len(pairs),
            "details": document.get("details", ""),
        },
    )

    write_json(
        out_dir / "compromised_versions.json",
        {
            "advisory_id": advisory.osv_id,
            "note": (
                "versions[] is authoritative; ranges[] over-match and are never "
                "expanded. ranges_present records where we saw them."
            ),
            "packages": [
                {
                    "name": package.name,
                    "ecosystem": package.ecosystem,
                    "versions": list(package.versions),
                    "affected_entries": package.entry_count,
                    "ranges_present": package.ranges_present,
                    "fixed_candidates": list(package.fixed_candidates),
                }
                for package in advisory.affected
            ],
            "pairs": [{"name": name, "version": version} for name, version in pairs],
        },
    )
    return advisory


def pin_repo_commit() -> str:
    """The malicious-packages commit the roster is built from.

    Pinned so the enumeration is reproducible: a stranger re-running this gets
    the same 172 records, not whatever upstream looks like that day.
    """
    result = subprocess.run(
        ["gh", "api", f"repos/{REPO}/commits/main", "--jq", ".sha"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def fetch_repo_tarball(commit: str, *, cache_dir: Path = CACHE_DIR) -> Path:
    """Download the repo at a pinned commit, cached by sha.

    Fetched with `gh api` rather than piping curl: piping the tarball through a
    shell corrupts the gzip stream (the leading bytes arrive UTF-8 mangled), so
    it must land in a file or come through a library.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"malicious-packages-{commit[:12]}.tar.gz"
    if path.exists() and path.stat().st_size > 1_000_000:
        return path

    with path.open("wb") as handle:
        subprocess.run(
            ["gh", "api", f"repos/{REPO}/tarball/{commit}"],
            stdout=handle,
            check=True,
        )
    return path


def iter_campaign_records(tarball: Path) -> Iterator[tuple[str, dict[str, Any]]]:
    """Stream the tarball, yielding (path, document) for campaign members only.

    Streamed rather than extracted: the archive holds ~235,000 records and we
    want 172 of them.
    """
    with tarfile.open(tarball, mode="r|gz") as archive:
        for member in archive:
            if not member.isfile() or not member.name.endswith(".json"):
                continue
            if "/osv/malicious/" not in member.name:
                continue
            handle = archive.extractfile(member)
            if handle is None:
                continue
            try:
                document = json.loads(handle.read())
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            member_of, reasons = is_campaign_member(document)
            if member_of:
                yield member.name, document


def build_campaign_artifact(*, out_dir: Path = INCIDENT_DIR) -> dict[str, Any]:
    """Enumerate the campaign and write data/incident/campaign.json."""
    commit = pin_repo_commit()
    tarball = fetch_repo_tarball(commit)

    members: list[dict[str, Any]] = []
    for repo_path, document in iter_campaign_records(tarball):
        affected = group_affected(document)
        if not affected:
            continue
        first = affected[0]
        _, reasons = is_campaign_member(document)
        members.append(
            {
                "name": first.name,
                # OSV spells it "PyPI"; the repo path is lowercase. Normalise
                # on the OSV form and keep the path for provenance.
                "ecosystem": first.ecosystem,
                "osv_id": document.get("id", ""),
                # 15 of 172 records have no alias at all, and `aliases` is null
                # rather than an empty list, so this cannot be indexed blindly.
                # None of them alias the umbrella advisory: the aliases are
                # per-package GHSAs, so joining on the umbrella matches nothing.
                "aliases": list(document.get("aliases") or ()),
                "versions": list(first.versions),
                "reasons": reasons,
                "repo_path": repo_path.split("/", 1)[-1],
            }
        )

    members.sort(key=lambda row: (row["ecosystem"], row["name"]))
    npm = [m for m in members if m["ecosystem"].lower() == "npm"]
    other = [m for m in members if m["ecosystem"].lower() != "npm"]
    pairs = sum(len(m["versions"]) for m in members)

    payload = {
        "source": f"{REPO}@{commit}",
        "umbrella_advisory": UMBRELLA_ID,
        "marker_sha256": CAMPAIGN_ORIGIN_SHA256,
        "marker_domain": CAMPAIGN_IOC_DOMAIN,
        "note": (
            "Enumerated by scanning every OSV record in the pinned commit for the "
            "campaign's shared origin hash. There is no shared alias: each record "
            "aliases its own per-package GHSA, and none alias the umbrella."
        ),
        "counts": {
            "records": len(members),
            "npm": len(npm),
            "other_ecosystems": len(other),
            "malicious_pairs": pairs,
            "without_alias": sum(1 for m in members if not m["aliases"]),
        },
        "members": members,
    }
    write_json(out_dir / "campaign.json", payload)
    return payload


def main(argv: Iterable[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Build incident ground truth")
    parser.add_argument("command", choices=["advisory", "campaign"])
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "advisory":
        advisory = build_advisory_artifacts()
        pairs = compromised_pairs(advisory)
        print(f"advisory  {advisory.osv_id}  ({advisory.cve})")
        print(f"packages  {len(advisory.affected)}")
        print(f"versions  {len(pairs)}")
        ranged = sum(1 for p in advisory.affected if p.ranges_present)
        print(f"packages with ranges we deliberately did not expand: {ranged}")

    if args.command == "campaign":
        payload = build_campaign_artifact()
        counts = payload["counts"]
        print(f"source    {payload['source']}")
        for key, value in counts.items():
            print(f"{key:18s} {value:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
