"""Bolt round-trip against a running HydraDB node.

Mirrors scripts/http_smoke.sh over Bolt, which is the transport the loader
uses. Exercises the constraints that shape every write we will make:

* auth is the basic scheme with username "neo4j" and the token as password
* explicit transactions are unsupported, so writes are autocommit session.run
* a mutation returns no rows, and that is success
"""

from __future__ import annotations

import os
import sys

from neo4j import GraphDatabase

BOLT_URI = os.environ.get("HYDRA_BOLT_URI", "neo4j://127.0.0.1:7687")
TOKEN = os.environ.get("HYDRA_TOKEN", "local-development-token-32-bytes")
DATABASE = os.environ.get("HYDRA_DATABASE", "default")

NODE_ID = 900000002
NODE_KEY = "smoke:bolt"

UPSERT = "UNWIND $rows AS row MERGE (n {id: row.vertex}) SET n:Smoke, n.key = row.key"
READ_BACK = "MATCH (n:Smoke {id: $id}) RETURN n.key AS key"
CLEANUP = "UNWIND $vertices AS row MATCH (n {id: row.vertex}) DETACH DELETE n"


def main() -> int:
    with GraphDatabase.driver(BOLT_URI, auth=("neo4j", TOKEN)) as driver:
        driver.verify_connectivity()
        print(f"== connected to {BOLT_URI}")

        with driver.session(database=DATABASE) as session:
            print("== Bolt write (batched UNWIND upsert)")
            result = session.run(UPSERT, rows=[{"vertex": NODE_ID, "key": NODE_KEY}])
            records = list(result)
            if records:
                print(f"FAIL: expected no rows from a mutation, got {records}", file=sys.stderr)
                return 1
            print("   OK: empty mutation envelope")

            print("== Bolt read-back")
            record = session.run(READ_BACK, id=NODE_ID).single()
            if record is None or record["key"] != NODE_KEY:
                print(f"FAIL: expected key '{NODE_KEY}', got {record}", file=sys.stderr)
                return 1
            print(f"   OK: read back key={record['key']!r}")

            session.run(CLEANUP, vertices=[{"vertex": NODE_ID}]).consume()
            print("   OK: smoke node deleted")

    print("Bolt smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
