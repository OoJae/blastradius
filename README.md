# BlastRadius

**When an npm package is compromised, BlastRadius tells you in seconds who is exposed.**

Paste a package name and get its complete transitive blast radius. Upload a
`package-lock.json` and find out whether your service resolved a malicious
version *while it was live*. Every answer is a graph traversal executed by
[HydraDB](https://github.com/hydra-db/hydradb).

Built for **Hack Hydra 2026, Track 2A (supply-chain blast radius)** and
demonstrated against the real Mini Shai-Hulud / TanStack compromise of
**May 11, 2026** — 84 malicious artifacts across 42 packages, published in a
six-minute window (19:20–19:26 UTC, CVE-2026-45321 / GHSA-g7cv-rxg3-hmpx).

> **Status: Phase 0 complete (verification).** This README is filled in as
> features land. Sections below are placeholders until the work they describe
> exists. Measured facts so far — including the constraints that shaped the
> schema — are in [docs/VERIFICATION.md](docs/VERIFICATION.md).

---

## The 6-minute problem

_TODO (Phase 3): the incident, in three sentences._

## A functional product

_TODO (Phase 3): deployed link + quickstart._

Quickstart (local). Requires Docker, [`just`](https://github.com/casey/just)
and [`uv`](https://docs.astral.sh/uv/):

```bash
just up            # start HydraDB (Docker) and wait for readiness
just smoke         # HTTP + Bolt round-trip against the running node
just ingest-demo   # load the checked-in fixture graph
just test          # unit tests (no database required)
just test-live     # check HydraDB's answers against the fixture's known answers
```

## Real ingestion and retrieval workflows

_TODO (Phase 1): pipeline diagram, node/edge counts, loader design._

## A clear use case

_TODO (Phase 3): incident-response walkthrough._

## A thoughtful technical implementation

_TODO (Phase 2): schema diagram and the constraint-to-design table._

## How HydraDB is used, and what we would lose without it

Every analytical answer is a HydraDB query, and every response carries the
statement that produced it and how long it took — `hydra: {cypher, ms, steps}`.
Two answers are not computed per request, and rather than leave that implicit,
here is exactly which is which.

### Live, on every request

| Answer | Query | Measured |
|---|---|---|
| Blast radius | `PKG_DEPENDED_BY*1..n` from a fixed seed | 172 ms at depth 1, 1,666 ms at depth 3 (5,782 packages) |
| Multi-source incident | one `algo.MSpaths` call seeded with all 42 compromised packages | 8.4 s, server-side, replacing 42 round trips |
| Maintainer overlap | two single-hop `MAINTAINS` statements | 61 ms |
| Typosquats | `SIMILAR_NAME` single hop | 6 ms |
| Lockfile advisory hits | `AFFECTS` on the resolved version, and the live-window comparison as an integer predicate on the edge | 7 ms per matching entry |

Every one of these is cached after the first call, and **`?fresh=1` forces a
re-run** — the response reports `cached` per step, so a cached number and a
computed one are never confusable. A live test asserts that `fresh=1` produces
an uncached traversal step.

### Cached HydraDB results, captured at startup

Autocomplete and the header counts. Both are read out of the graph when the
service boots, and both are caches of HydraDB result sets rather than a second
query engine: the prefix lookup is a binary search over names HydraDB gave us
and **cannot return a name HydraDB did not**.

We do this because the graph-native forms are measurably too slow for the job:

| | Graph-native | Served from |
|---|---|---|
| Prefix search | **4,940 ms** | 0.018 ms binary search |
| `MATCH (n:Package) RETURN count(*)` | **2,423 ms** | boot-time value |

This Cypher subset has no index DDL, so there is no faster graph-native form
available. Both statements are still in the repo
(`api/queries/q11_prefix_search.cypher`, `q8_label_count.cypher`), still
executed by `just parse-check` on every run, and **`/api/suggest?live=1` runs
the real prefix query** so the cache can be diffed against the graph rather
than trusted. A live test does exactly that.

### Not from HydraDB at all

The `PKG_DEPENDS_ON` edge count. An unanchored count over 1.3M edges is refused
outright:

```
cypher_edge_rows rejected by admission control: actual 250001 exceeds limit 250000
```

`/api/stats` shows that refusal verbatim next to the loader's manifest number,
rather than quietly substituting one for the other. Anchored traversals over
the same edges run in milliseconds; it is counting *everything* that is
impossible, not traversing.

### What we would lose without it

The product is a reverse-dependency closure over a versioned package graph with
a temporal predicate on the advisory edge. Vector search cannot express it —
"who transitively depends on this" is not a similarity question. SQL can, with
a recursive CTE, but the shape of the answer is exactly what a graph database
is for, and the constraints HydraDB imposed made the model *better*: because a
variable-length pattern must be anchored on a fixed source, the loader
materialises a reverse projection, which is what makes the blast radius a
single anchored traversal rather than a scan.

### One correction to our own framing

The brief describes this incident as a blast radius growing over a six-minute
window. The timestamps disagree, and the product says so: the attack was **two
automated bursts of 42 versions, four seconds each, five and a half minutes
apart**. Every affected package had a malicious version live within the first
four seconds, so the exposed set reached its full **8,555 packages three
seconds in** and did not move again. `/api/incident` reports the waves and the
saturation point; the exposure curve is computed from the same traversals the
blast radius uses.

## Why this is only possible with a graph

_TODO (Phase 3): see docs/WHY_A_GRAPH.md._

## Results

_TODO (Phase 2): eval report against the real incident's advisory ground truth._

## Attribution

- **[HydraDB](https://github.com/hydra-db/hydradb)** (AGPL-3.0) — used as an
  external database service over Bolt and HTTP. No HydraDB source is vendored
  into this repository.
- Further dataset and library attribution is added as each source lands
  (deps.dev, OSV.dev, OpenSSF malicious-packages, npm registry).
- Built with [Claude Code](https://claude.com/claude-code).

## License

MIT — see [LICENSE](LICENSE).
