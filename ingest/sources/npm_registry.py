"""npm registry crawler: maintainers, publish times, and unpublished versions.

The registry is the only source for two things the dependency data cannot give
us: who maintains a package (the worm's actual propagation vector), and when a
version was published -- including versions that no longer exist.

That last point is what makes the incident's timeline recoverable at all. The
malicious versions were unpublished, so they are absent from the `versions` map
and from every downstream dataset, but they remain in the registry's `time`
map. Reconciling the two maps is how we date the attack.

Full package documents are large (one incident package is 3.5 MiB), so the
cache stores a projection: maintainers, the time map, and the set of version
keys. That is everything we need and roughly a hundredth of the bytes.
"""

from __future__ import annotations

import asyncio
import gzip
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import httpx

from ingest.ids import hash_key

REGISTRY = "https://registry.npmjs.org"
DOWNLOADS = "https://api.npmjs.org/downloads/point/last-week"

# The bulk downloads endpoint accepts at most this many packages, and rejects
# scoped names outright -- which matters here because most campaign victims are
# scoped and therefore have to be requested one at a time.
BULK_CAP = 128

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache" / "npm"

# Keys in the `time` map that are not versions.
TIME_META = frozenset({"created", "modified", "unpublished"})


@dataclass(frozen=True)
class PackageDigest:
    """The parts of a registry document we keep."""

    name: str
    status: int
    branch: str  # ok | tombstoned | notfound | error
    maintainers: tuple[str, ...]
    time: dict[str, str]
    version_keys: frozenset[str]

    def publish_times(self) -> dict[str, str]:
        return {k: v for k, v in self.time.items() if k not in TIME_META}

    def unpublished_versions(self) -> frozenset[str]:
        """Versions the registry has dated but no longer serves.

        A version present in `time` but absent from `versions` was published
        and then withdrawn. For this incident that set is exactly the malicious
        artifacts.
        """
        return frozenset(self.publish_times()) - self.version_keys


def registry_url(name: str) -> str:
    # Scoped names must have their slash escaped, or the registry reads the
    # scope as a path segment.
    return f"{REGISTRY}/{name.replace('/', '%2f')}"


def cache_path(name: str, *, root: Path = CACHE_DIR) -> Path:
    shard = f"{hash_key(name):016x}"[:2]
    safe = name.replace("/", "%2f")
    return root / shard / f"{safe}.json.gz"


def project(name: str, document: dict, *, status: int) -> PackageDigest:
    """Reduce a full registry document to the fields we store."""
    time_map = document.get("time", {}) or {}
    unpublished = time_map.get("unpublished")

    if isinstance(unpublished, dict):
        # A fully-unpublished package: the versions live inside the tombstone
        # block rather than in a `versions` map.
        maintainers = unpublished.get("maintainers", []) or []
        version_keys = frozenset(unpublished.get("versions", []) or [])
        branch = "tombstoned"
    else:
        maintainers = document.get("maintainers", []) or []
        version_keys = frozenset((document.get("versions") or {}).keys())
        branch = "ok"

    names = tuple(
        sorted(
            m.get("name", "") if isinstance(m, dict) else str(m)
            for m in maintainers
            if m
        )
    )
    return PackageDigest(
        name=name,
        status=status,
        branch=branch,
        maintainers=tuple(n for n in names if n),
        time={k: v for k, v in time_map.items() if isinstance(v, str)},
        version_keys=version_keys,
    )


def _write_cache(digest: PackageDigest, *, root: Path) -> None:
    path = cache_path(digest.name, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "name": digest.name,
        "status": digest.status,
        "branch": digest.branch,
        "maintainers": list(digest.maintainers),
        "time": digest.time,
        "version_keys": sorted(digest.version_keys),
    }
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle)


def _read_cache(name: str, *, root: Path) -> PackageDigest | None:
    path = cache_path(name, root=root)
    if not path.exists():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    return PackageDigest(
        name=payload["name"],
        status=payload["status"],
        branch=payload["branch"],
        maintainers=tuple(payload["maintainers"]),
        time=payload["time"],
        version_keys=frozenset(payload["version_keys"]),
    )


async def _fetch_one(
    client: httpx.AsyncClient,
    name: str,
    *,
    root: Path,
    semaphore: asyncio.Semaphore,
    attempts: int = 4,
) -> PackageDigest:
    cached = _read_cache(name, root=root)
    if cached is not None:
        return cached

    async with semaphore:
        for attempt in range(1, attempts + 1):
            try:
                response = await client.get(registry_url(name))
            except httpx.HTTPError:
                if attempt == attempts:
                    digest = PackageDigest(name, 0, "error", (), {}, frozenset())
                    _write_cache(digest, root=root)
                    return digest
                await asyncio.sleep(0.5 * 2**attempt)
                continue

            if response.status_code == 404:
                # A negative result is a real signal (package removed), so it
                # is cached rather than retried.
                digest = PackageDigest(name, 404, "notfound", (), {}, frozenset())
                _write_cache(digest, root=root)
                return digest

            if response.status_code in (408, 425, 429, 500, 502, 503, 504):
                if attempt == attempts:
                    digest = PackageDigest(
                        name, response.status_code, "error", (), {}, frozenset()
                    )
                    _write_cache(digest, root=root)
                    return digest
                retry_after = response.headers.get("retry-after")
                delay = float(retry_after) if retry_after else 0.5 * 2**attempt
                await asyncio.sleep(min(delay, 30.0))
                continue

            response.raise_for_status()
            digest = project(name, response.json(), status=response.status_code)
            _write_cache(digest, root=root)
            return digest

    digest = PackageDigest(name, 0, "error", (), {}, frozenset())
    _write_cache(digest, root=root)
    return digest


async def fetch_digests(
    names: Sequence[str],
    *,
    root: Path = CACHE_DIR,
    concurrency: int = 8,
    progress_every: int = 250,
) -> dict[str, PackageDigest]:
    semaphore = asyncio.Semaphore(concurrency)
    results: dict[str, PackageDigest] = {}

    limits = httpx.Limits(max_connections=concurrency * 2, max_keepalive_connections=concurrency * 2)
    timeout = httpx.Timeout(60.0, connect=10.0, read=120.0)
    async with httpx.AsyncClient(
        limits=limits,
        timeout=timeout,
        headers={"User-Agent": "BlastRadius/0.1 (Hack Hydra 2026)"},
        follow_redirects=True,
    ) as client:
        tasks = [
            _fetch_one(client, name, root=root, semaphore=semaphore) for name in names
        ]
        done = 0
        for coro in asyncio.as_completed(tasks):
            digest = await coro
            results[digest.name] = digest
            done += 1
            if progress_every and done % progress_every == 0:
                print(f"  registry: {done}/{len(names)}", flush=True)

    return results


def fetch_digests_sync(names: Sequence[str], **kwargs) -> dict[str, PackageDigest]:
    return asyncio.run(fetch_digests(names, **kwargs))


def maintainer_edges(digests: Iterable[PackageDigest]) -> list[tuple[str, str]]:
    """(maintainer username, package name) pairs."""
    edges: list[tuple[str, str]] = []
    for digest in digests:
        for username in digest.maintainers:
            edges.append((username, digest.name))
    return edges
