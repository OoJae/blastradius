"""Failures must be explained, not swallowed.

The strings below are the literal messages this server produced during Phases 0
and 1, recorded in docs/VERIFICATION.md. They are pinned here because the whole
point of classifying them is to tell "the database refused" apart from "we sent
something invalid" -- and to keep either from being reported as an empty result.
"""

from __future__ import annotations

import asyncio

import pytest

from api.hydra import NotComputed, classify

ADMISSION = (
    "{neo4j_code: Neo.TransientError.General.MemoryPoolOutOfMemoryError} "
    "{message: cypher_edge_rows rejected by admission control: actual 250001 "
    "exceeds limit 250000}"
)
BATCH_ADMISSION = (
    "{neo4j_code: Neo.TransientError.General.MemoryPoolOutOfMemoryError} "
    "{message: client_query_batch_items rejected by admission control: actual 1025 "
    "exceeds limit 1024}"
)
RUNTIME_TIMEOUT = (
    "{neo4j_code: Neo.ClientError.Transaction.Terminated} "
    "{message: client_query_runtime exceeded query timeout after 119999 ms}"
)
FIXED_SOURCE = (
    "{neo4j_code: Neo.ClientError.Statement.InvalidSyntax} "
    "{message: OpenCypher query is not supported yet: variable-length MATCH "
    "requires a fixed source id}"
)
COMPOSITE_PARAM = (
    "{neo4j_code: Neo.ClientError.Statement.InvalidSyntax} "
    "{message: ClientProtocol query is not supported yet: composite parameter "
    "$vals is only supported as an UNWIND input}"
)
DEFUNCT = (
    "Failed to read from defunct connection ResolvedIPv4Address(('127.0.0.1', 7687))"
)


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        (ADMISSION, "admission_control"),
        (BATCH_ADMISSION, "admission_control"),
        (RUNTIME_TIMEOUT, "timeout"),
        (FIXED_SOURCE, "rejected"),
        (COMPOSITE_PARAM, "rejected"),
        (DEFUNCT, "unavailable"),
    ],
)
def test_measured_failures_are_classified(text: str, kind: str) -> None:
    assert classify(RuntimeError(text)).kind == kind


def test_deadline_is_its_own_kind() -> None:
    # Distinct from a server-side timeout: this one is our own patience running
    # out, and the query may still be running.
    assert classify(asyncio.TimeoutError()).kind == "deadline"


@pytest.mark.parametrize(
    ("kind", "retryable"),
    [
        ("admission_control", False),
        ("rejected", False),
        ("timeout", True),
        ("unavailable", True),
        ("deadline", True),
    ],
)
def test_retryability_matches_the_cause(kind: str, retryable: bool) -> None:
    # An admission refusal will refuse again; a syntax rejection is our bug.
    # Neither is worth retrying, and saying so stops a client from hammering.
    assert NotComputed(kind, "x").retryable is retryable


def test_the_failing_statement_travels_with_the_failure() -> None:
    failure = classify(
        RuntimeError(ADMISSION),
        cypher="MATCH (s:Package)-[r:PKG_DEPENDS_ON]->(d:Package) RETURN count(*) AS n",
        ms=2400.0,
        hint="depth 2 returned 2,772 packages in 712 ms",
    )
    assert "PKG_DEPENDS_ON" in failure.cypher
    assert failure.ms == 2400.0
    assert failure.as_dict()["hint"].startswith("depth 2")


def test_an_unknown_error_is_still_classified_rather_than_raised_raw() -> None:
    # Fail closed: something we have never seen becomes an explainable 503,
    # never an unhandled exception that reaches the client as a bare 500.
    failure = classify(RuntimeError("something nobody has seen before"))
    assert failure.kind in ("unavailable", "rejected", "timeout", "admission_control")
    assert failure.message


def test_classification_never_loses_the_message() -> None:
    for text in (ADMISSION, RUNTIME_TIMEOUT, FIXED_SOURCE, DEFUNCT):
        assert classify(RuntimeError(text)).message
