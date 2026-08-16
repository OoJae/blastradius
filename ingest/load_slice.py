"""Load a compiled slice into HydraDB.

Streams gzipped CSV straight into batched writes, so peak memory stays flat
regardless of slice size. Nodes are written before edges because an edge batch
matches both endpoints by id: if an endpoint does not exist yet, the MATCH
finds nothing and the batch writes zero rows without reporting an error.

PKG_DEPENDED_BY is produced by re-reading the same file with the endpoints
swapped rather than from a second file, so the forward and reverse projections
cannot drift apart.
"""

from __future__ import annotations

import csv
import gzip
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ingest import hydra
from ingest.ids import (
    adv_key,
    hash_key,
    mnt_key,
    pkg_key,
    rel_id,
    ver_key,
)
from ingest.load import BatchWriter, LoadReport


@dataclass
class StageResult:
    name: str
    rows: int
    seconds: float

    @property
    def rate(self) -> float:
        return self.rows / self.seconds if self.seconds else 0.0


def read_csv(path: Path) -> Iterator[dict[str, str]]:
    with gzip.open(path, "rt", newline="", encoding="utf-8") as handle:
        yield from csv.DictReader(handle)


def count_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with gzip.open(path, "rt", newline="", encoding="utf-8") as handle:
        return max(sum(1 for _ in handle) - 1, 0)


# --- row generators -------------------------------------------------------


def package_rows(slice_dir: Path) -> Iterator[dict]:
    for row in read_csv(slice_dir / "packages.csv.gz"):
        key = pkg_key(row["name"])
        yield {
            "vertex": hash_key(key),
            "key": key,
            "name": row["name"],
            "is_popular": row["is_popular"] == "1",
            # Weekly downloads are only measured for a subset; -1 marks "not
            # measured" so it can never be mistaken for a real reading of zero.
            "weekly_downloads": -1,
        }


def version_rows(slice_dir: Path) -> Iterator[dict]:
    for row in read_csv(slice_dir / "versions.csv.gz"):
        key = ver_key(row["name"], row["version"])
        yield {
            "vertex": hash_key(key),
            "key": key,
            "name": row["name"],
            "version": row["version"],
            "published_at": int(row["published_at"]),
            "compromised": row["compromised"] == "1",
        }


def maintainer_rows(slice_dir: Path) -> Iterator[dict]:
    for row in read_csv(slice_dir / "maintainers.csv.gz"):
        key = mnt_key(row["username"])
        yield {"vertex": hash_key(key), "key": key, "username": row["username"]}


def advisory_rows(slice_dir: Path) -> Iterator[dict]:
    for row in read_csv(slice_dir / "advisories.csv.gz"):
        key = adv_key(row["key"])
        yield {
            "vertex": hash_key(key),
            "key": key,
            "cve": row["cve"],
            "severity": float(row["severity"]),
            "summary": row["summary"],
            "published_at": int(row["published_at"]),
        }


def pkg_edge_rows(slice_dir: Path, rel_type: str, *, flip: bool) -> Iterator[dict]:
    for row in read_csv(slice_dir / "pkg_depends_on.csv.gz"):
        src_name, dst_name = row["src"], row["dst"]
        if flip:
            src_name, dst_name = dst_name, src_name
        src, dst = pkg_key(src_name), pkg_key(dst_name)
        yield {
            "src": hash_key(src),
            "dst": hash_key(dst),
            "rel": rel_id(rel_type, src, dst),
            "via_versions": int(row["via_versions"] or 1),
        }


def similar_name_rows(slice_dir: Path) -> Iterator[dict]:
    for row in read_csv(slice_dir / "similar_name.csv.gz"):
        src, dst = pkg_key(row["src"]), pkg_key(row["dst"])
        yield {
            "src": hash_key(src),
            "dst": hash_key(dst),
            "rel": rel_id("SIMILAR_NAME", src, dst),
            "distance": int(row["distance"]),
        }


def version_of_rows(slice_dir: Path) -> Iterator[dict]:
    for row in read_csv(slice_dir / "version_of.csv.gz"):
        src, dst = ver_key(row["name"], row["version"]), pkg_key(row["name"])
        yield {"src": hash_key(src), "dst": hash_key(dst), "rel": rel_id("VERSION_OF", src, dst)}


def maintains_rows(slice_dir: Path) -> Iterator[dict]:
    for row in read_csv(slice_dir / "maintains.csv.gz"):
        src, dst = mnt_key(row["username"]), pkg_key(row["name"])
        yield {"src": hash_key(src), "dst": hash_key(dst), "rel": rel_id("MAINTAINS", src, dst)}


def affects_rows(slice_dir: Path) -> Iterator[dict]:
    for row in read_csv(slice_dir / "affects.csv.gz"):
        src = adv_key(row["advisory"])
        dst = ver_key(row["name"], row["version"])
        yield {
            "src": hash_key(src),
            "dst": hash_key(dst),
            "rel": rel_id("AFFECTS", src, dst),
            "live_from": int(row["live_from"]),
            "live_until": int(row["live_until"]),
        }


def load(slice_dir: Path, *, verbose: bool = True, only: set[str] | None = None) -> LoadReport:
    report = LoadReport()
    results: list[StageResult] = []

    node_stages = [
        ("Package", ["key", "name", "is_popular", "weekly_downloads"], package_rows, "packages.csv.gz"),
        ("Version", ["key", "name", "version", "published_at", "compromised"], version_rows, "versions.csv.gz"),
        ("Maintainer", ["key", "username"], maintainer_rows, "maintainers.csv.gz"),
        ("Advisory", ["key", "cve", "severity", "summary", "published_at"], advisory_rows, "advisories.csv.gz"),
    ]
    edge_stages = [
        ("PKG_DEPENDS_ON", "Package", "Package", ["via_versions"],
         lambda d: pkg_edge_rows(d, "PKG_DEPENDS_ON", flip=False), "pkg_depends_on.csv.gz"),
        ("PKG_DEPENDED_BY", "Package", "Package", ["via_versions"],
         lambda d: pkg_edge_rows(d, "PKG_DEPENDED_BY", flip=True), "pkg_depends_on.csv.gz"),
        ("VERSION_OF", "Version", "Package", [], version_of_rows, "version_of.csv.gz"),
        ("MAINTAINS", "Maintainer", "Package", [], maintains_rows, "maintains.csv.gz"),
        ("AFFECTS", "Advisory", "Version", ["live_from", "live_until"], affects_rows, "affects.csv.gz"),
        ("SIMILAR_NAME", "Package", "Package", ["distance"], similar_name_rows, "similar_name.csv.gz"),
    ]

    with hydra.connect() as driver:
        writer = BatchWriter(driver)

        for label, props, generator, filename in node_stages:
            if only and label not in only:
                continue
            path = slice_dir / filename
            if not path.exists():
                if verbose:
                    print(f"  {label:16s} SKIPPED (no {filename})")
                continue
            started = time.perf_counter()
            stats = writer.upsert_nodes(label, props, generator(slice_dir))
            elapsed = time.perf_counter() - started
            report.nodes.merge(stats)
            results.append(StageResult(label, stats.rows, elapsed))
            if verbose:
                print(f"  {label:16s} {stats.rows:>9,} nodes  {stats.rows/elapsed if elapsed else 0:>8,.0f}/s", flush=True)

        for rel_type, src_label, dst_label, props, generator, filename in edge_stages:
            if only and rel_type not in only:
                continue
            path = slice_dir / filename
            if not path.exists():
                if verbose:
                    print(f"  {rel_type:16s} SKIPPED (no {filename})")
                continue
            started = time.perf_counter()
            stats = writer.upsert_edges(rel_type, src_label, dst_label, props, generator(slice_dir))
            elapsed = time.perf_counter() - started
            report.edges.merge(stats)
            results.append(StageResult(rel_type, stats.rows, elapsed))
            if verbose:
                print(f"  {rel_type:16s} {stats.rows:>9,} edges  {stats.rows/elapsed if elapsed else 0:>8,.0f}/s", flush=True)

    (slice_dir / "load_report.json").write_text(
        json.dumps(
            {r.name: {"rows": r.rows, "seconds": round(r.seconds, 1), "rate": round(r.rate)} for r in results},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return report


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Load a compiled slice into HydraDB")
    parser.add_argument("--dir", required=True)
    parser.add_argument(
        "--only",
        help="comma-separated stage names to run, for resuming after an interruption",
    )
    args = parser.parse_args()

    slice_dir = Path(args.dir)
    only = {s.strip() for s in args.only.split(",")} if args.only else None
    print(f"loading {slice_dir}" + (f" (stages: {sorted(only)})" if only else ""))
    report = load(slice_dir, only=only)
    print(report.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
