"""Connection helpers shared by the loader and (from Phase 2) the API."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from neo4j import Driver, GraphDatabase

# HydraDB rejects an UNWIND batch larger than this. Measured exactly: 1024 is
# accepted, 1025 is rejected by admission control on client_query_batch_items.
MAX_BATCH_ROWS = 1024

# The server caps a query at 1 MiB including parameters. Leave headroom for the
# statement text and PackStream framing.
MAX_BATCH_BYTES = 900_000


def bolt_uri() -> str:
    """The Bolt endpoint.

    Locally this is the routed `neo4j://` scheme. Across containers it must be
    the direct `bolt://` scheme instead: routing discovery returns the server's
    advertised address, which HydraDB reports as 127.0.0.1, so a client in a
    different container would be sent back to itself and fail with
    "No read service currently available".
    """
    return os.environ.get("HYDRA_BOLT_URI", "neo4j://127.0.0.1:7687")


def auth_token() -> str:
    return os.environ.get("HYDRA_TOKEN", "local-development-token-32-bytes")


def database() -> str:
    return os.environ.get("HYDRA_DATABASE", "default")


def connect() -> Driver:
    """Open a driver.

    Auth is the basic scheme with a fixed username and the token as password;
    HydraDB only checks that the principal is non-empty. Requires the 6.x
    driver: 5.x refuses the handshake because the server agent is not `Neo4j/`.
    """
    return GraphDatabase.driver(bolt_uri(), auth=("neo4j", auth_token()))


@contextmanager
def session(driver: Driver) -> Iterator:
    """A session on the configured database.

    Explicit transactions are unsupported, so callers use autocommit
    `session.run`; each statement commits to object storage on its own.
    """
    with driver.session(database=database()) as sess:
        yield sess


def cypher_string_literal(value: str) -> str:
    """Render a Python string as a Cypher single-quoted literal.

    Needed because the path procedures reject driver parameters for their list
    and label options: `sourceValues` must appear inline in the query text.
    """
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def cypher_string_list(values: list[str]) -> str:
    return "[" + ", ".join(cypher_string_literal(v) for v in values) + "]"
