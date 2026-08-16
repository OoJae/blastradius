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

### 5. Accepted and rejected statement shapes

Measured with `scripts/probe_shapes.py`, `scripts/probe_traversal.py` and
`scripts/probe_singlehop.py` against the running node. HydraDB has no
network-reachable `EXPLAIN`, so every shape below was executed for real.

**Batched writes**

| Shape | Verdict |
|---|---|
| `MERGE (n {id: row.vertex}) SET n:Label, n.key = row.key` | accepted |
| Label inside the MERGE pattern: `MERGE (n:Label {id: row.vertex})` | rejected — *UNWIND vertex upsert MERGE pattern matches only id* |
| `SET n.compromised = true` (literal) | rejected — *UNWIND vertex SET values must read fields from the row map* |
| `SET n.compromised = row.compromised` (value carried per row) | accepted |
| Endpoints matched with one comma-separated `MATCH` | accepted |
| Endpoints matched with two separate `MATCH` clauses | rejected — *UNWIND batches support CREATE or MATCH followed by…* |
| Unlabelled endpoints | rejected — *endpoints require exactly one label* |
| Relationship `MERGE` **without** an id | rejected — *UNWIND relationship MERGE requires id: row.\<field\>* |
| Relationship `MERGE (s)-[r:T {id: row.rel}]->(d) SET r.p = row.p` | accepted |

**Every relationship needs its own deterministic id.** This is not in the
project plan and it is not optional. `ingest/ids.py` therefore hashes an edge
natural key (`<TYPE>:<src_key>-><dst_key>`) exactly as it does for nodes, so
re-running the loader is idempotent rather than duplicating edges.

**Batch-row ceiling, measured exactly**

| Rows per UNWIND | Verdict |
|---|---|
| 1000 | accepted |
| **1024** | accepted |
| 1025 | rejected |
| 2000, 5000 | rejected |

The rejection arrives as
`Neo.TransientError.General.MemoryPoolOutOfMemoryError: client_query_batch_items
rejected by admission control: actual 1025 exceeds…`. Note it is reported as a
*transient* error, so a naive retry loop would spin forever on an
oversized batch; the loader treats it as fatal and splits instead. The project
plan's assumed 5k–10k row batches were never going to work.

### 6. Variable-length MATCH must be anchored on a fixed **source** id

This is the single most consequential finding of Phase 0, because it
invalidates the planned blast-radius query.

| Formulation | Verdict |
|---|---|
| `MATCH (d:Package)-[:PKG_DEPENDS_ON*1..4]->(c:Package {id: $id})` | **rejected** — *variable-length MATCH requires a fixed source id* |
| `MATCH (c:Package {id: $id})<-[:PKG_DEPENDS_ON*1..4]-(d:Package)` | **rejected** — same error |
| `MATCH (c:Package {id: $id})-[:PKG_DEPENDED_BY*1..4]->(d:Package)` | accepted, correct closure |
| `MATCH (m:Maintainer)-[:MAINTAINS]->(p:Package {id: $id})` (single hop) | accepted |
| `MATCH (m:Maintainer)-[:MAINTAINS*1..1]->(p:Package {id: $id})` | **rejected** — the restriction is about var-length syntax, not hop count |

A blast radius asks "who depends on me", which is naturally
destination-anchored, and reversing the arrow in the pattern does not help.
The fix is a **materialised reverse projection**: alongside
`PKG_DEPENDS_ON` (dependent → dependency) the loader writes
`PKG_DEPENDED_BY` (dependency → dependent), so the radius becomes an outgoing
traversal from a fixed seed. Approved as a v1 schema change on 2026-08-16.

Single-hop patterns are unaffected, so maintainer overlap (Q4), advisory
lookups (Q3) and typosquats (Q5) keep their planned destination-anchored form.

**Aggregates over a radius:** `count(*)`, `collect(...)` and
`RETURN DISTINCT d.key, d.id LIMIT n` all work. `count(DISTINCT d.id)` does
not — *DISTINCT aggregate arguments are not executable* — so distinct counts
are computed client-side from a DISTINCT projection.

### 7. Path procedures behave as documented, with one hard limit

On the **forward** edge, with no reverse projection needed:

| Call | Verdict |
|---|---|
| `algo.SSpaths({sourceNode: $id, relDirection: 'incoming', …})` | accepted |
| `algo.MSpaths({sourceLabel, sourceProperty, sourceValues: ['pkg:npm/…'], relDirection: 'incoming', …})` | accepted |
| Same call with `sourceValues: $vals` as a driver parameter | **rejected** — *composite parameter $vals is only supported as an UNWIND input* |
| `pathCount: 0` (all minimum-weight paths) | accepted — returned both branches of a diamond |

`relDirection: 'incoming'` works, which is what the multi-source query needs.
Numeric options (`maxLen`, `pathCount`, `resultLimit`) accept driver
parameters; **the list and label options must be inline literals**, so the API
needs a small escaping helper to interpolate seed keys into the query text.

### 8. `pathCount` is a shortest-paths budget, not a traversal depth

This one cost real time and changes how the product uses the procedures.
Measured on the toy graph (`scripts/toy_mspaths.py`), incoming from `p0` with
`maxLen: 4`, where the true reverse closure is 5 nodes reachable over 8 simple
paths:

| `pathCount` | paths returned | path lengths | nodes covered |
|---|---|---|---|
| 0 | 2 | 1, 1 | p1, p2 |
| 1 | 1 | 1 | p1 |
| 3 | 3 | 1, 1, 2 | p1, p2, p3 |
| 5 | 5 | 1, 1, 2, 2, 3 | p1…p4 |
| 200 | 8 | 1, 1, 2, 2, 3, 3, 4, 4 | p1…p5 |

`pathCount: 0` does **not** mean unlimited: it keeps only the paths tied at the
minimum weight, and since an unweighted edge weighs 1, that is the one-hop
paths. `pathCount: N` keeps the N cheapest paths overall, so a value that is
too small silently returns a shallow slice of the radius. The value saturates
once every simple path within `maxLen` has been enumerated.

**Consequence for the product.** The two mechanisms answer different questions
and both are needed:

* **The closure set** (how many packages are exposed, and which) comes from a
  variable-length `MATCH` over `PKG_DEPENDED_BY`. It returns distinct nodes,
  so a diamond is counted once.
* **The paths** (the chains drawn in the UI, and the one multi-source call
  seeded with every compromised package) come from `algo.MSpaths`. Path count
  grows combinatorially with fan-in — p5 above is reachable by two paths
  through a single diamond — so the UI asks for a bounded sample rather than
  the full enumeration, and the count shown to the user always comes from the
  `MATCH` side.

Verified alongside it: the seed label filters only the seed vertices (a Version
key mixed into Package seeds is ignored rather than erroring), and seeding with
Version keys over a Package-to-Package edge returns zero paths — so the
multi-source incident query seeds with the 42 compromised **package** keys, not
the 84 version keys.

---

## Measurements

`scripts/spike_load.py`, run against an empty store with nothing else touching
the node. 100,000 packages in a 7-layer DAG, each node depending on three in
the layer below (one pick biased toward the layer's first ten, so hubs and
diamonds exist), written in both directions: **599,400 edges**.

### Write throughput

| Phase | Rows | Wall | Rate | Retries |
|---|---|---|---|---|
| Nodes | 100,000 | 1.8 s | **56,198 nodes/s** | 0 |
| Edges (both directions) | 599,400 | 125.6 s | **4,771 edges/s** | 0 |

Edges cost roughly twelve times more than nodes per row, which is expected: an
edge batch matches both endpoints by id before merging. Batches were the
maximum 1024 rows and nothing was rejected. Object store on disk afterwards:
**1.6 GB** for 699,400 rows, about 2.3 KB per row.

### Traversal latency, and the truncation check

Each depth was run once cold, then five times to take a warm median. The
`returned` column is what HydraDB gave back; `oracle` is the same closure
computed independently in Python from the generated edge list.

| Depth | Cold | Warm (p50) | Returned | Oracle | Verdict |
|---|---|---|---|---|---|
| 1 | 5 ms | 4 ms | 89 | 89 | exact |
| 2 | 79 ms | 47 ms | 1,172 | 1,172 | exact |
| 3 | 479 ms | 427 ms | 6,903 | 6,903 | exact |
| 4 | 2,009 ms | 1,666 ms | 25,403 | 25,403 | exact |
| 5 | 5,162 ms | 5,057 ms | 55,403 | 55,403 | exact |
| 6 | 10,413 ms | 10,954 ms | 90,403 | 90,403 | exact |

**No silent truncation at any depth.** The depth-6 radius covers 90,403 nodes —
90% of the graph — and still matched the oracle exactly, so neither the 250k
intermediate-rows frontier cap nor the scan-edge budget bit at this scale. That
is a real answer to the risk the plan flagged, not an assumption.

Latency, however, tracks the size of the result: a radius returning 90k nodes
takes about eleven seconds. Depths 1–3 are comfortably interactive; depth 4 is
usable; depths 5–6 are not, for a hub this large.

### Path procedures

| Call | Result | Cold | Warm |
|---|---|---|---|
| `algo.SSpaths`, incoming, `pathCount: 200` | 200 paths | 816 ms | **9 ms** |
| `algo.MSpaths`, 10 seeds, incoming, `pathCount: 200` | 1,000 paths | 1,199 ms | **33 ms** |

The multi-source call resolves ten seeds server-side in 33 ms warm. This is the
query the incident view is built on, and it is fast because a bounded
`pathCount` caps the work regardless of how large the radius is.

### One thing that is too slow

Prefix search — `WHERE p.key STARTS WITH …` with `LIMIT 10` — took **1,831 ms
warm** over 100k nodes. It appears to scan rather than use an index, and there
is no index DDL in this Cypher subset. At the scale we plan to load, live
typeahead against the graph is not viable, so autocomplete needs a different
mechanism (a precomputed in-process prefix table over the popular-package list
is the obvious one). Recorded here rather than discovered during the demo.

## Phase 1 corrections (measured on real data)

Phase 0's numbers came from a 100k-node graph on an almost-empty store. At real
scale several of them do not hold, and two of the differences are structural
rather than a matter of degree.

### The local object store cannot sustain a bulk load

`CLOUD_PROVIDER=local` -- the backend HydraDB's own README uses for
development -- does not implement conditional writes:

```
Operation `put_opts` with mode `PutMode::Update` not yet implemented
by LocalFileSystem(file:///data/store)
```

Under sustained writes this is fatal rather than cosmetic. The storage manifest
cannot be updated, so the writer lease lapses, the node fences itself and exits
**cleanly (exit code 0, not an OOM kill)** mid-load; separately, batches fail
with `internal query execution error` when a flush coincides with a write.
Eight retries backing off to 20s did not clear it -- the limitation is
persistent, not a transient collision.

Phase 0 never saw this: a 55-edge fixture and a 600k-edge spike do not sustain
enough write pressure. It takes a multi-million-edge load to surface.

**Fix: MinIO.** HydraDB is object-store-native, so pointing it at S3-compatible
storage is both the remedy and closer to how it is designed to run.
`docker-compose.yml` now runs MinIO alongside it. The writer-lease and
heartbeat ceilings are also raised to their maximums, since fencing exists to
stop two writers colliding and a single-node dev setup has no second writer.

### Write throughput is ~2.7x lower than the spike suggested

| Phase | Measurement |
|---|---|
| Phase 0 spike (100k nodes, local store) | 56,198 nodes/s, **4,771 edges/s** |
| Phase 1 real load (52k packages, MinIO) | 14,737 nodes/s, **1,755 edges/s** |

The spike measured an empty graph on a local disk. Against a multi-gigabyte
store with periodic compaction, over an S3 API, edges cost roughly three times
more. **Edge count, not node count, is what bounds a slice**: every edge is a
durable object-storage write, and the 3.4M writes for this slice took 32
minutes.

This is why the loaded slice is ~52k packages rather than the 750k that Phase 0
arithmetic implied. The incident core is unaffected -- all 42 compromised
packages, all 84 malicious versions, and the full reverse-dependency halo
around them are present; what shrinks is the popularity spine, which
contributes the most edges for the least demonstrative value.

### The 250k intermediate-row cap errors; it does not truncate silently

This was the risk flagged as most dangerous, because a quiet short answer is
indistinguishable from a correct one. Measured behaviour is the reassuring
case:

```
cypher_edge_rows rejected by admission control: actual 250001 exceeds limit 250000
```

An unanchored `count(*)` over a 1.68M-edge relationship type is **refused**,
not silently shortened. Anchored traversals over the same edges are unaffected,
so this is a limit on counting everything rather than on the product's queries.
`verify_counts` reports such a count as `skipped` and relies on the traversal
oracle, which compares topology rather than cardinality.

### A package can be in its own blast radius

The first oracle run showed HydraDB returning one node *more* than the Python
oracle for `typescript` at depth 2. HydraDB was right: with devDependencies
included, npm has cycles, and `typescript` genuinely reaches itself in two
reverse hops. The oracle had excluded the root by construction. Corrected --
a node is recorded when reached, and only traversal is deduplicated.

### Verified results, demo slice

52,042 packages / 1,684,557 edges (both directions written), plus the incident
overlay.

| Check | Result |
|---|---|
| Referential integrity (offline preflight) | every edge endpoint exists |
| Node and small-edge counts vs source files | exact |
| Reverse-closure oracle, 12 packages x depths 1-2 | **24/24 exact** |
| Deep probe, `typescript` depth 3 | 49,371 nodes, oracle agrees, 58.5 s |

Blast radius of `@tanstack/react-router`:

| Depth | Dependents | Time |
|---|---|---|
| 1 | 1,034 | 245 ms |
| 2 | 2,772 | 1,073 ms |
| 3 | 7,947 | 2,922 ms |

One `algo.MSpaths` call seeded with all 42 compromised packages returns 2,000
paths across 1,134 distinct packages in 8.4 s -- server-side, replacing 42
client round trips. Depth 1-2 is interactive; depth 3 and the multi-source call
want precomputing for a live demo.

### Two Cypher-text quirks, both found by executing the query files

There is no network-reachable EXPLAIN, so `just parse-check` executes every
query file at every value its whitelist permits, against a sentinel id that
exists in no graph. It caught two things a reader would not guess:

1. **Comments are `//`, not `--`.** SQL-style comments fail every statement with
   `Invalid input '-': expected ';', a statement option, a query hint, a clause
   or a schema command`.
2. **A path procedure cannot have a leading comment.** HydraDB dispatches
   `algo.SSpaths` / `algo.MSpaths` only when the *trimmed* statement begins with
   `CALL`, so any preceding comment produces `query transport cannot authorize
   an unsupported Cypher clause` — a message that points nowhere near the real
   cause. Verified by sending the identical query with and without its comment
   block: rejected, then accepted.

So the query files keep their explanatory comments for anyone reading the repo,
and `render()` strips them before the statement is sent.

## Slice tier recommendation

Using the measured rates, with a 1.3 overhead factor for wider real rows,
retries and the projection pass:

    projected_load = (nodes / 56,198 + edges / 4,771) × 1.3

| Tier | Nodes | Edges written (incl. reverse projection) | Projected load |
|---|---|---|---|
| T1 | 2.5 M | ~20 M | ~1 h 32 m |
| T2 | 750 k | ~5 M | ~23 m |
| T3 | 250 k | ~1.7 M | ~8 m |

**Load throughput is not the binding constraint — every tier fits inside an
evening.** Two other things are:

1. **Result-size latency.** A radius of 90k nodes costs ~11 s. Popular npm
   packages have far more dependents than that, so on a T1 slice a deep radius
   on a hub package would be slower than a demo can absorb.
2. **The frontier cap we have not yet hit.** 250k intermediate rows is not
   configurable. We verified it does not bite at 90k results; we have no
   evidence about 500k, and the failure mode is a *quiet* short answer.

**Recommendation: T2** (~750k nodes), with a default demo depth of 3 and the
depth control offering 1–6. That keeps radius sizes inside the range where
truncation has actually been disproved, keeps the headline queries under half a
second, and still loads in under half an hour — leaving Phase 1's real
bottleneck, data acquisition, the time it needs. If acquisition finishes early,
growing toward T1 is a re-run of the loader plus a re-run of the oracle check,
not a redesign.
