"""The query registry, and the only sanctioned way to interpolate into Cypher.

Two things in this Cypher subset cannot be driver parameters: a variable-length
depth bound, and the option lists of a path procedure. Both therefore have to be
written into the query text, which makes this module the injection surface for
the whole service.

It is closed two ways. A placeholder may only take a value that is identical to
a member of a declared whitelist -- not merely matching a pattern -- and the
seed list may only come from `seed_values()`, which returns a distinct type that
`render()` type-checks. A package name typed by a user is therefore checked
against the graph before it is escaped, and escaped before it is rendered.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ingest.hydra import cypher_string_list
from ingest.ids import pkg_key

QUERY_DIR = Path(__file__).resolve().parent

# Cypher uses braces for map literals, so placeholders need a delimiter that
# cannot collide with the query language itself.
PLACEHOLDER = re.compile(r"<<(\w+)>>")

# Traversal depth is part of the pattern grammar, so it is interpolated. Six is
# the deepest the UI offers; the server would accept more but nothing needs it.
ALLOWED_DEPTHS: tuple[int, ...] = (1, 2, 3, 4, 5, 6)

# Only labels whose count is cheap enough to take at boot. Edge counts are
# absent on purpose: an unanchored count over the dependency edges is refused.
COUNTABLE_LABELS: frozenset[str] = frozenset(
    {"Package", "Version", "Maintainer", "Advisory"}
)


class UnsafeInterpolation(ValueError):
    """A value was not a member of its placeholder's whitelist."""


class CypherLiteral(str):
    """Text already escaped for inlining, produced only by `seed_values()`.

    A distinct type rather than a plain string so `render()` can tell escaped
    text from a caller's string, which is what stops an unescaped list from
    reaching a query by mistake.
    """

    __slots__ = ()


@dataclass(frozen=True)
class QuerySpec:
    name: str
    template: str
    # placeholder -> the values it may take. `None` means the value must be a
    # CypherLiteral rather than a member of a fixed set.
    interpolations: Mapping[str, frozenset[Any] | None]


def _load(name: str) -> str:
    return (QUERY_DIR / f"{name}.cypher").read_text()


def statement_of(template: str) -> str:
    """The executable statement, with the explanatory comments removed.

    The comments stay in the files, because the reason a query is shaped the way
    it is matters more than the query. They cannot be sent, though: HydraDB
    dispatches a path procedure only when the *trimmed* statement begins with
    `CALL`, so a leading comment makes `algo.SSpaths` and `algo.MSpaths` fail
    with "query transport cannot authorize an unsupported Cypher clause".
    Stripping uniformly also means the inspector shows exactly what was
    executed.
    """
    return "\n".join(
        line
        for line in template.splitlines()
        if line.strip() and not line.strip().startswith("//")
    )


_INTERPOLATIONS: dict[str, dict[str, frozenset[Any] | None]] = {
    "q1_radius_nodes": {"depth": frozenset(ALLOWED_DEPTHS)},
    "q2_incident_paths": {"seed_values": None},
    "q8_label_count": {"label": COUNTABLE_LABELS},
}

REGISTRY: dict[str, QuerySpec] = {
    path.stem: QuerySpec(
        name=path.stem,
        template=_load(path.stem),
        interpolations=_INTERPOLATIONS.get(path.stem, {}),
    )
    for path in sorted(QUERY_DIR.glob("*.cypher"))
}


def render(name: str, **interpolate: Any) -> str:
    """Render a query, refusing anything not explicitly permitted."""
    spec = REGISTRY[name]
    allowed = spec.interpolations

    missing = set(allowed) - set(interpolate)
    if missing:
        raise UnsafeInterpolation(f"{name}: missing interpolation {sorted(missing)}")
    extra = set(interpolate) - set(allowed)
    if extra:
        raise UnsafeInterpolation(f"{name}: unexpected interpolation {sorted(extra)}")

    for key, value in interpolate.items():
        permitted = allowed[key]
        if permitted is None:
            if not isinstance(value, CypherLiteral):
                raise UnsafeInterpolation(
                    f"{name}: {key} must come from seed_values(), got {type(value).__name__}"
                )
            continue
        # Identity against the whitelist, not a pattern match: `2` passes,
        # `"2"` and `"2]->(x)-[*1..8"` do not.
        if not any(value == option and type(value) is type(option) for option in permitted):
            raise UnsafeInterpolation(
                f"{name}: {key}={value!r} is not one of {sorted(permitted)}"
            )

    rendered = statement_of(spec.template)
    for key, value in interpolate.items():
        rendered = rendered.replace(f"<<{key}>>", str(value))
    if PLACEHOLDER.search(rendered):
        raise UnsafeInterpolation(f"{name}: unrendered placeholder remains")
    return rendered


def seed_values(names: Sequence[str], known: Iterable[str] | None = None) -> CypherLiteral:
    """Escape package names for inlining into a path-procedure option list.

    When `known` is supplied -- the set of names the graph actually holds --
    anything absent is dropped before escaping, so a name invented by a caller
    never reaches the query text at all.
    """
    if known is not None:
        allowed = set(known)
        names = [name for name in names if name in allowed]
    if not names:
        raise UnsafeInterpolation("seed_values: no known package names supplied")
    return CypherLiteral(cypher_string_list([pkg_key(name) for name in sorted(set(names))]))
