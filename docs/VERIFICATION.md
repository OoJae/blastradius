# Verification log

Everything here was measured or observed against a real HydraDB node, not
inferred from documentation. Where the project plan assumed something that
turned out to be wrong, the delta is recorded rather than quietly fixed.

- **HydraDB version:** 0.1.0 (`ghcr.io/hydra-db/hydradb:latest`,
  digest `sha256:db78309a…cdb709`), image architecture `arm64`.
- **Host:** macOS (Apple Silicon), 16 GB RAM, 10 CPU, Docker 29.4.3.
- **Client:** Python 3.13.14, `neo4j` driver 6.2.0, `xxhash` 4.0.0.

---

## Phase 0 findings

### 1. The Neo4j 5.x Python driver cannot talk to HydraDB at all

`neo4j==5.28.4` fails during the Bolt handshake:

```
neo4j.exceptions.UnsupportedServerProduct: SlateDBGraph/0.1.0
```

The 5.x driver validates the server agent string and rejects anything that is
not `Neo4j/…`. Driver **6.2.0 removed that check** and connects successfully;
it still negotiates Bolt 5.1–5.4, which is what HydraDB serves
(`src/client/bolt.rs`: `BOLT_SUPPORTED_VERSIONS = [(5,4),(5,3),(5,2),(5,1)]`).

This is not a workaround we invented: HydraDB's own
`scripts/ec2_graphblas_benchmark.sh` installs `neo4j==6.2.0` to drive its
benchmark client.

**Consequence:** `pyproject.toml` pins `neo4j>=6.2,<7`. The project plan's
"pin to 5.x because the server speaks Bolt 5.x" reasoning was backwards —
driver major version and Bolt protocol version are independent.

### 2. Mutations return an empty envelope, and that is success

Confirmed on both transports. An UNWIND upsert over HTTP returns:

```json
{"query_id":"http-query-4","columns":[],"rows":[],"read_epoch":null,
 "bookmark":"sgk:1:64656661756c74:64656661756c74:63656c6c2d30:3"}
```

Over Bolt the result yields zero records. The loader must treat this as a
successful write; the smoke tests assert it explicitly so a future refactor
cannot regress into "empty result means failure".

### 3. Startup requirements confirmed

The node reported ready at `GET :9090/readyz` (HTTP 200, empty body) **1 second**
after `docker compose up -d`, with `graph node listeners started`
(bolt `0.0.0.0:7687`, http `0.0.0.0:8443`, admin `0.0.0.0:9090`) and no errors
or warnings in the log.

Settings that are load-bearing rather than cosmetic, all now in
`docker-compose.yml`:

| Setting | Why |
|---|---|
| `RUST_MIN_STACK=33554432` | Without it the node serves `/readyz` and then aborts with a stack overflow on the first query |
| `user: "$(id -u):$(id -g)"` | The image runs as UID 10001 and cannot write the bind mount otherwise |
| `LOCAL_PATH=/data/store` on a pre-created directory | The node does not create it |
| `GRAPH_ALLOW_PLAINTEXT=true` | Local development only; TLS is otherwise required |

Raised from their defaults for traversal headroom:
`GRAPH_MAX_QUERY_SCAN_EDGES=100000000` (default 1M) and
`GRAPH_MAX_QUERY_RUNTIME_MS=120000` (default 30s).

Not overridable by environment, and therefore a hard ceiling on how wide a
blast radius can get: `max_query_intermediate_rows` (250k frontier) and
`max_query_index_candidates` (250k). The throughput spike probes whether these
bite at our scale.

### 4. Container healthcheck: not used

`just up` polls `:9090/readyz` from the host instead of relying on a Docker
healthcheck, because the image is not guaranteed to ship `curl` or `wget`.
The host-side poll is the authoritative readiness gate.

---

## Pending

- MSpaths path-procedure semantics on a toy graph (direction, seeding, labels).
- Write throughput (rows/sec) and traversal latency by depth.
- Silent-truncation probe against a Python ground-truth oracle.
- Slice tier recommendation.
