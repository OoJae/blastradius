"""Read a package-lock and say what the graph knows about what it resolved.

The verdict rules are the careful part. A lockfile is evidence of what a machine
actually installed, so the answers have to distinguish four states rather than
two, and in particular must never say "clean" when the honest answer is "we hold
no record of this".

Only the packages an advisory names have version-level records, so most entries
in a real lockfile are legitimately unprovable. Reporting those as clean would
be the single most misleading thing this product could do.

`decide_entry` and `rollup` take no client and do no I/O, which is what lets the
fixture contract be pinned by an offline test.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

# Most severe first. Used for sorting and for the file-level rollup.
VERDICTS = ("EXPOSED", "UNKNOWN", "AT_RISK", "CLEAN")
RANK = {verdict: index for index, verdict in enumerate(VERDICTS)}

MAX_BYTES = 4_000_000


class LockfileError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class LockEntry:
    name: str
    version: str
    paths: tuple[str, ...]
    dev: bool = False

    @property
    def occurrences(self) -> int:
        return len(self.paths)


@dataclass(frozen=True)
class ParsedLockfile:
    service: str | None
    lockfile_version: int
    entries: tuple[LockEntry, ...]
    sha256: str
    skipped: tuple[tuple[str, str], ...]


def name_from_path(path: str) -> str | None:
    """The package a lockfile path refers to.

    Takes the part after the *last* `node_modules/`, so a nested duplicate
    resolves to the package itself rather than its parent, and a scoped name
    keeps its scope.
    """
    marker = "node_modules/"
    index = path.rfind(marker)
    if index == -1:
        return None
    name = path[index + len(marker) :]
    return name or None


def parse_package_lock(document: Mapping[str, Any]) -> ParsedLockfile:
    version = document.get("lockfileVersion")
    if not isinstance(version, int):
        raise LockfileError("not_a_lockfile", "no lockfileVersion field; is this a package-lock.json?")
    packages = document.get("packages")
    if not isinstance(packages, dict):
        if version == 1:
            raise LockfileError(
                "v1_unsupported",
                "package-lock v1 has no `packages` map. Run `npm install` with "
                "npm 7 or newer to upgrade the lockfile, then try again.",
            )
        raise LockfileError("not_a_lockfile", "no `packages` map")

    seen: dict[tuple[str, str], list[str]] = {}
    dev_flags: dict[tuple[str, str], bool] = {}
    skipped: list[tuple[str, str]] = []

    for path, meta in packages.items():
        if not isinstance(meta, dict):
            skipped.append((path, "not an object"))
            continue
        if path == "":
            skipped.append((path, "the root project itself"))
            continue
        if meta.get("link"):
            skipped.append((path, "workspace link, not an installed artifact"))
            continue
        resolved_version = meta.get("version")
        if not resolved_version:
            skipped.append((path, "no resolved version"))
            continue
        name = meta.get("name") or name_from_path(path)
        if not name:
            skipped.append((path, "could not determine a package name"))
            continue

        key = (name, str(resolved_version))
        seen.setdefault(key, []).append(path)
        dev_flags[key] = dev_flags.get(key, True) and bool(meta.get("dev"))

    if not seen:
        raise LockfileError("no_entries", "the lockfile resolved no packages")

    entries = tuple(
        LockEntry(name=name, version=version_, paths=tuple(sorted(paths)), dev=dev_flags[(name, version_)])
        for (name, version_), paths in sorted(seen.items())
    )
    root = packages.get("", {}) if isinstance(packages.get(""), dict) else {}
    return ParsedLockfile(
        service=root.get("name") or document.get("name"),
        lockfile_version=version,
        entries=entries,
        sha256=hashlib.sha256(
            json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        skipped=tuple(skipped),
    )


def parse_bytes(blob: bytes) -> ParsedLockfile:
    if len(blob) > MAX_BYTES:
        raise LockfileError("too_large", f"lockfile exceeds {MAX_BYTES // 1_000_000} MB")
    try:
        document = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise LockfileError("not_json", f"could not parse JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise LockfileError("not_a_lockfile", "top level is not an object")
    return parse_package_lock(document)


@dataclass(frozen=True)
class EntryVerdict:
    name: str
    version: str
    verdict: str
    reason: str
    occurrences: int = 1
    advisory: dict[str, Any] | None = None
    window: str | None = None
    signals: dict[str, Any] = field(default_factory=dict)


def decide_entry(
    entry: LockEntry,
    *,
    advisory_hit: dict[str, Any] | None,
    package_in_graph: bool,
    version_in_graph: bool,
    exposed_names: frozenset[str] | None,
    exposed_complete: bool,
    installed_at: int | None = None,
) -> EntryVerdict:
    """What the graph can say about one resolved artifact.

    Evaluated so that positive evidence comes before absence of evidence: a
    package proven to be in the blast radius stays AT_RISK even if we happen to
    hold no record of its exact version.
    """
    signals = {
        "advisory_hit": advisory_hit is not None,
        "package_in_graph": package_in_graph,
        "version_in_graph": version_in_graph,
        "closure_complete": exposed_complete,
    }

    if advisory_hit is not None:
        window = None
        if installed_at is not None:
            live_from = advisory_hit.get("live_from")
            live_until = advisory_hit.get("live_until")
            if live_from is not None and live_until is not None:
                if installed_at < live_from:
                    window = "before"
                elif installed_at > live_until:
                    window = "after"
                else:
                    window = "inside"
        return EntryVerdict(
            entry.name,
            entry.version,
            "EXPOSED",
            "this exact version is named by an advisory",
            entry.occurrences,
            advisory=advisory_hit,
            window=window,
            signals=signals,
        )

    if exposed_names is not None and exposed_complete and entry.name in exposed_names:
        return EntryVerdict(
            entry.name,
            entry.version,
            "AT_RISK",
            "this package transitively depends on a compromised package",
            entry.occurrences,
            signals=signals,
        )

    if not package_in_graph:
        return EntryVerdict(
            entry.name, entry.version, "UNKNOWN",
            "this package is outside the loaded slice", entry.occurrences, signals=signals,
        )
    if exposed_names is None or not exposed_complete:
        return EntryVerdict(
            entry.name, entry.version, "UNKNOWN",
            "the blast radius could not be computed, so safety cannot be proved",
            entry.occurrences, signals=signals,
        )
    if not version_in_graph:
        return EntryVerdict(
            entry.name, entry.version, "UNKNOWN",
            "no version-level record is held for this package", entry.occurrences, signals=signals,
        )

    return EntryVerdict(
        entry.name, entry.version, "CLEAN",
        "in the graph, no advisory names it, and it is outside the blast radius",
        entry.occurrences, signals=signals,
    )


def rollup(verdicts: Sequence[EntryVerdict]) -> dict[str, Any]:
    """The file-level answer, which is a different question from the entries.

    An entry verdict says what the graph knows about an artifact. The file
    verdict answers "did this service install a malicious artifact?" -- so an
    AT_RISK entry, which is a statement about a dependency rather than an
    install, does not by itself make the file anything worse than clean. The
    fixture contract pins exactly this.
    """
    counts = {verdict: 0 for verdict in VERDICTS}
    for entry in verdicts:
        counts[entry.verdict] += 1

    if counts["EXPOSED"]:
        verdict = "EXPOSED"
        why = f"{counts['EXPOSED']} resolved artifact(s) are named by an advisory."
    elif counts["UNKNOWN"]:
        verdict = "UNKNOWN"
        why = (
            f"No resolved artifact is named by an advisory, but {counts['UNKNOWN']} "
            "entries cannot be proved safe from what this graph holds."
        )
    else:
        verdict = "CLEAN"
        why = "No resolved artifact is named by an advisory, and every entry was checkable."

    if counts["AT_RISK"]:
        why += (
            f" {counts['AT_RISK']} package(s) sit inside the blast radius, meaning they "
            "depend on something compromised even though the versions installed here "
            "are not themselves malicious."
        )

    return {
        "verdict": verdict,
        "determinate": counts["UNKNOWN"] == 0,
        "why": why,
        "counts": counts,
    }
