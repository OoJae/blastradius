"""Stop the evaluation from reaching its own answer key.

The interesting claim this project makes is that graph traversal finds exposed
packages. That claim is void if the evaluation quietly consults the advisory
that lists them. The temptation is real and easy to yield to by accident: the
advisory is right there in the same graph, one hop from the seeds.

So discovery queries are checked before they reach the driver. The check sits
on the session rather than at the call sites, which means every helper --
including the traversal code reused from `ingest.verify_counts` -- is covered
without being modified, and a new query cannot be added that bypasses it.

Two policies, because "cheating" depends on what is being claimed:

* Discovery may traverse dependencies, maintainers and name similarity only.
  Reading the advisory here would be circular.
* The product may read the advisory, because answering "is this pinned version
  malicious" *from the advisory* is the advisory doing its job. That is a
  feature, not a shortcut.

The rule that holds in both: the answer key never appears in a query string.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

# Relationship types written as `[:TYPE]`, and as `relTypes: ['TYPE']` inside a
# path-procedure call -- the procedure form has no bracket pattern to match, so
# both spellings have to be extracted or the guard has a blind spot.
_REL_PATTERN = re.compile(r"\[\s*[a-zA-Z_]*\s*:\s*([A-Z][A-Z0-9_]*)")
_REL_TYPES_OPTION = re.compile(r"relTypes\s*:\s*\[([^\]]*)\]")
_LABEL_PATTERN = re.compile(r"\(\s*[a-zA-Z_]*\s*:\s*([A-Z][A-Za-z0-9_]*)")
_SOURCE_LABEL_OPTION = re.compile(r"sourceLabel\s*:\s*'([^']*)'")
_QUOTED = re.compile(r"'([^']*)'")

# A literal shorter than this is too likely to occur inside an unrelated
# identifier to be denied safely.
MIN_DENIABLE_LITERAL = 4


class ForbiddenQuery(RuntimeError):
    """A query tried to use something the current claim does not permit."""

    def __init__(self, rule: str, matched: str, query: str) -> None:
        super().__init__(f"{rule}: {matched!r} is not permitted here\n  query: {query}")
        self.rule = rule
        self.matched = matched
        self.query = query


class PolicyError(ValueError):
    """The policy itself is unsafe to enforce."""


@dataclass(frozen=True)
class Policy:
    name: str
    allowed_rel_types: frozenset[str]
    allowed_labels: frozenset[str]
    denied_tokens: frozenset[str]
    denied_literals: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        for literal in self.denied_literals:
            if len(literal) < MIN_DENIABLE_LITERAL:
                raise PolicyError(
                    f"denied literal {literal!r} is too short to match safely; "
                    "it would fire on unrelated identifiers. Review it explicitly."
                )

    def check(self, query: str) -> None:
        """Raise unless the query stays inside this policy. Fails closed."""
        lowered = query.lower()

        for token in sorted(self.denied_tokens):
            if token.lower() in lowered:
                raise ForbiddenQuery("denied_token", token, query)

        for literal in sorted(self.denied_literals):
            if literal.lower() in lowered:
                raise ForbiddenQuery("answer_key_literal", literal, query)

        used_rels = set(_REL_PATTERN.findall(query))
        for option in _REL_TYPES_OPTION.findall(query):
            used_rels.update(_QUOTED.findall(option))
        for rel in sorted(used_rels):
            if rel not in self.allowed_rel_types:
                raise ForbiddenQuery("relationship_type", rel, query)

        used_labels = set(_LABEL_PATTERN.findall(query))
        used_labels.update(_SOURCE_LABEL_OPTION.findall(query))
        for label in sorted(used_labels):
            if label not in self.allowed_labels:
                raise ForbiddenQuery("label", label, query)


# Tokens that would let a discovery query read the incident's answer key.
_ADVISORY_TOKENS = frozenset(
    {"AFFECTS", "Advisory", "compromised", "adv:", "live_from", "live_until"}
)


def discovery_policy(victim_names: Iterable[str] = ()) -> Policy:
    """For claims of the form 'traversal found this'."""
    return Policy(
        name="discovery",
        allowed_rel_types=frozenset(
            {"PKG_DEPENDS_ON", "PKG_DEPENDED_BY", "MAINTAINS", "SIMILAR_NAME"}
        ),
        allowed_labels=frozenset({"Package", "Maintainer"}),
        denied_tokens=_ADVISORY_TOKENS,
        denied_literals=frozenset(_deniable(victim_names)),
    )


def product_policy(victim_names: Iterable[str] = ()) -> Policy:
    """For the lockfile verdict, where consulting the advisory is the point."""
    return Policy(
        name="product",
        allowed_rel_types=frozenset(
            {
                "PKG_DEPENDS_ON",
                "PKG_DEPENDED_BY",
                "MAINTAINS",
                "SIMILAR_NAME",
                "AFFECTS",
                "VERSION_OF",
                "RESOLVES",
            }
        ),
        allowed_labels=frozenset(
            {"Package", "Maintainer", "Version", "Advisory", "Service"}
        ),
        denied_tokens=frozenset(),
        denied_literals=frozenset(_deniable(victim_names)),
    )


def _deniable(names: Iterable[str]) -> list[str]:
    return [name for name in names if len(name) >= MIN_DENIABLE_LITERAL]


class GuardedSession:
    """A session that refuses out-of-policy queries and records the rest.

    The transcript is written so the guard can be audited rather than trusted:
    every query the evaluation ran is on disk next to its results.
    """

    def __init__(self, session, policy: Policy, transcript: Path | None = None) -> None:
        self._session = session
        self._policy = policy
        self._transcript = transcript
        self._queries: list[str] = []

    @property
    def policy(self) -> Policy:
        return self._policy

    @property
    def queries(self) -> list[str]:
        return list(self._queries)

    def run(self, query: str, **params):
        self._policy.check(query)
        self._queries.append(query)
        if self._transcript is not None:
            self._transcript.parent.mkdir(parents=True, exist_ok=True)
            with self._transcript.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"policy": self._policy.name, "query": query}) + "\n")
        return self._session.run(query, **params)
