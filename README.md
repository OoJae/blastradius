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

> Every number in this README was measured, not estimated. The constraints that
> shaped the schema, the throughput figures, and the things that turned out
> differently from what we assumed are all in
> [docs/VERIFICATION.md](docs/VERIFICATION.md).

---

## The 6-minute problem

On 11 May 2026 an attacker published 84 malicious versions across 42
`@tanstack/*` packages. `@tanstack/react-router` alone has about 19 million
weekly downloads. If your CI ran `npm install` that evening, the question you
needed answered in minutes was: *which of my services just installed one?*

That question is a transitive reverse-dependency closure over millions of
versioned nodes. A vector index cannot express it. In SQL it is a recursive CTE
over tens of millions of rows. It is a graph traversal, so this is built on a
graph.

**One correction to our own framing.** The brief for this project — and the
advisory's own prose — describe a six-minute window, and we set out to animate
a blast radius growing across it. The registry timestamps say something more
interesting. The attack was **two automated bursts of 42 versions, four seconds
each, five and a half minutes apart**; every affected package had a malicious
version live within the first four seconds, so the blast radius was complete
almost immediately and flat for the remaining 336 seconds. A gradual bloom would
have been a better animation and a worse fact, so the product shows the two
bursts.

## A functional product

**Live: https://api-production-ea37c.up.railway.app**

The hosted instance runs a reduced slice — 19,161 packages rather than 52,161 —
because its object store has to fit a 5 GB volume. The incident core is
identical, so every capability works; the blast radius of
`@tanstack/react-router` is **2,918 packages** there against **5,782** locally.
Both numbers are real traversals over their own graph.

Search a compromised package and the blast radius paints in stages — depth 1 at
about 170 ms, then 2, then 3 — so the screen is alive while the deeper traversal
runs. Drop in a `package-lock.json` and every resolved artifact gets a verdict,
including whether it was installed *while the malicious version was live* — and
every EXPOSED entry carries its next step: the earliest clean release after the
window, read from the graph, never guessed. The **next wave** tab answers the
question that follows: the worm published with stolen maintainer credentials, so
where can those credentials still publish? Seeded with only the *first*
compromised artifact, the maintainer pivot flags **41 of the 41** packages that
fell in the following minutes — and 6 candidates remain, `@tanstack/react-query`
among them with a radius of 8,971, larger than the incident itself. An
inspector drawer shows the exact Cypher behind every number, its milliseconds,
and whether it was live or cached.

Quickstart (local). Requires Docker, [`just`](https://github.com/casey/just),
[`uv`](https://docs.astral.sh/uv/) and Node:

```bash
just up            # start HydraDB + MinIO and wait for readiness
just ingest-demo   # load the checked-in fixture graph (30 nodes, known answers)
just test          # 181 unit tests, no database required
just test-live     # check HydraDB's answers against the fixture's known answers
just parse-check   # execute every .cypher file: the only way to parse-check here

just web-install   # one-time UI dependencies
just web-build     # build the interface into web/dist
just dev           # serve API + interface on http://127.0.0.1:8000
```

To run the real incident rather than the fixtures, build a slice from the
committed extract and load it — see *Real ingestion and retrieval workflows*.

## Real ingestion and retrieval workflows

```
deps.dev on BigQuery ──┐
  NPMRequirements      │   21.6 GB scanned, one query
  PackageVersions      ├─→ 46M npm dependency edges ─→ slice compiler ─→ HydraDB
OSV + OpenSSF ─────────┤     4.26M real packages        (CSR, 2 passes)   (batched
  172 campaign records │                                                   UNWIND)
npm registry ──────────┘
  maintainers, publish times
```

**Extraction.** `NPMRequirements` rather than `DependencyGraphEdges`: it is
npm-only, carries the declared semver ranges, and costs **21.6 GB** against
1,006 GB for the graph-edges table. It also avoids a trap — in
`DependencyGraphEdges`, `Name` is the *root* of a resolved tree and `From→To`
is any edge inside it, so grouping on `Name→To.Name` yields a pre-computed
transitive closure mislabelled as dependency edges. Depth 1 would already have
equalled the full blast radius.

**Two filters that matter.** deps.dev encodes bundled dependencies as
`parent>version>child`; those are **62% of the roster** and are not npm
packages — the registry 404s on them. And zero-dependency packages produce no
manifest rows at all, so the package roster comes from `PackageVersions`, never
from the edge list, or every leaf would vanish and its edges would silently
write nothing.

**Loading.** Batched `UNWIND` over Bolt, ≤1024 rows per batch (the measured
server cap; 1,025 is refused). Nodes before edges, because an edge batch whose
endpoint is missing matches nothing and writes zero rows *without erroring*.
`PKG_DEPENDED_BY` is produced by re-reading the forward file with endpoints
swapped, so the two projections cannot drift apart.

| | Local slice | Hosted slice |
|---|---:|---:|
| Packages | 52,161 | 19,161 |
| Dependency edges (written both ways) | 1,314,935 | 409,751 |
| Versions / maintainers | 16,802 / 2,707 | 16,802 / 2,707 |
| Advisory artifacts | 84 | 84 |

The hosted instance runs the smaller slice because its object store has to fit
a 5 GB volume. The incident core is identical in both; the popularity spine is
what shrinks.

**Verification, not assertion.** `ingest/verify_counts.py` checks every edge
endpoint exists *before* loading, compares every count against the source
files, and then compares HydraDB's reverse closures against the same closures
computed in pure Python — **24/24 probes exact**, including a package that
reaches itself through a dependency cycle.

## A clear use case

An advisory lands naming 42 compromised packages. You have minutes.

1. **What is the blast radius?** Searching `@tanstack/react-router` returns
   **5,782 packages** within three hops that would have pulled in a malicious
   version — 1,034 of them directly. Every number is a live traversal, and the
   statement that produced it is one click away.
2. **When was it dangerous?** The live window is not read from the advisory's
   prose; it is derived from the registry's own publish timestamps for all 84
   malicious versions: **19:20:39 → 19:26:19 UTC**. That is 19 seconds later at
   the close than the advisory text says, so an install in that gap is exposed
   under the data and clean under the prose.
3. **Was I hit?** Drop your lockfile. Every resolved artifact is checked against
   the advisory, and for anything matching, HydraDB evaluates the window as an
   integer predicate on the `AFFECTS` edge — the graph answers "were you
   installing while it was live", not the application.
4. **Where else should I look?** Shared maintainers and near-identical names are
   both one hop away — different questions from the blast radius, and the
   product keeps them apart rather than conflating them.

## A thoughtful technical implementation

```
(:Version)-[:VERSION_OF]->(:Package)
(:Package)-[:PKG_DEPENDS_ON  {via_versions}]->(:Package)
(:Package)-[:PKG_DEPENDED_BY {via_versions}]->(:Package)   ← materialised reverse
(:Maintainer)-[:MAINTAINS]->(:Package)
(:Advisory)-[:AFFECTS {live_from, live_until}]->(:Version) ← the temporal edge
(:Package)-[:SIMILAR_NAME {distance}]->(:Package)
```

Every one of these shapes exists because a measured constraint forced it. This
is the whole design, and none of it was guessed:

| Constraint (measured) | What it forced |
|---|---|
| A variable-length `MATCH` must anchor on a fixed **source** id; reversing the arrow is rejected identically | `PKG_DEPENDED_BY`, a materialised reverse projection. A blast radius is destination-anchored by nature and would otherwise be inexpressible |
| Property values are int/float/bool/string only | Semver ranges resolved to concrete versions at ingest; the live window stored as two epoch ints on the edge, which is what makes the temporal question a graph predicate |
| An `UNWIND` batch caps at 1024 rows, and reports overflow as a *transient* error | Batches of ≤1024; a naive retry loop would spin forever |
| Batched `SET` values must all read from the row map — no literals | Constants written into every row rather than inlined |
| Depth bounds cannot be parameters; path-procedure lists cannot be either | A whitelist for depths and a single escaping helper for seed lists — the entire injection surface, closed in one module |
| No functions, no `IN`, no `count(DISTINCT …)` | Typosquat distances materialised at ingest; distinct counts taken client-side |
| No network-reachable `EXPLAIN` | `just parse-check` **executes** every query file at every whitelist variant against a sentinel id |
| An unanchored `count(*)` over 1.3M edges is refused by admission control | The count is reported as refused, verbatim, rather than substituted |

Two further findings that only executing could reveal: Cypher comments are
`//` and not `--`, and a **path procedure cannot have a leading comment at
all**, because dispatch requires the trimmed statement to begin with `CALL`.
The query files keep their explanatory comments and `render()` strips them
before dispatch.

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
| Remediation target | `VERSION_OF` anchored on the package, filtered to clean releases after the window | ~660 ms per exposed entry (1,192 versions filtered) |

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

The **next-wave forecast** is also computed once at boot and served from
memory: 42 anchored `MAINTAINS` hops out, 3 back, then one
`PKG_DEPENDED_BY*1..3` radius per reachable package — 51 statements whose
traces are replayed, ages attached, into every `/api/forecast` response. Its
checkable claim is measured rather than asserted: seeded with only the first
artifact, the pivot flags 41/41 of the packages that fell afterwards, and the
same payload states what the pivot *cannot* do — 0 of the 128 broader-campaign
victims are reachable this way, because phishing is not an edge in this graph.

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

The short version: a blast radius is a transitive, directional, unbounded-depth
closure, and the live-window check is a predicate on a *relationship* rather
than on either endpoint. SQL can express the first by reimplementing a reverse
index per query; vector search cannot express it at all, because dependency is
not similarity and a plausible wrong answer is worse than none.

The full argument, with the measured costs and the exact statements, is in
**[docs/WHY_A_GRAPH.md](docs/WHY_A_GRAPH.md)**.

## Results

Full report, generated from the run rather than typed:
**[docs/EVAL_REPORT.md](docs/EVAL_REPORT.md)**.

**Exposure enumeration is exact.** HydraDB and an independent Python oracle
agree on every package at every depth — zero missing, zero extra:

| Depth | Packages exposed | Median per seed |
|---|---:|---:|
| 1 | 1,321 | 3 ms |
| 2 | 5,828 | 24 ms |
| 3 | **8,555** | 112 ms |

**Campaign victim recall is 0 of 119, and that is the interesting result.**
Across 12.2M dependency edges, **no package in any victim organisation depends
on any `@tanstack` package**. The same scan does see 137 `@tanstack` packages
being depended on and 2,722 packages depending on them, so the zero is a
property of the data rather than a broken query.

The worm spread by stealing CI credentials: a developer installed a compromised
package, their tokens leaked, and the attacker published to packages they owned
— packages that never declared a dependency on TanStack. **No dependency
traversal can follow that, and the report says so rather than finding a metric
that flatters the tool.** Every one of the 172 campaign records gets exactly one
stated reason, and the counts reconcile.

The evaluation cannot cheat: a guard refuses any discovery query that mentions
the advisory, the `compromised` flag, or a victim name, checked *before* the
driver is touched. The obvious shortcut is planted in the source and a test
asserts it is refused.

## Attribution

**Database**

- **[HydraDB](https://github.com/hydra-db/hydradb)** (AGPL-3.0) — used as an
  external database service over Bolt and HTTP. No HydraDB source is vendored
  into this repository.

**Datasets**

- **[deps.dev](https://deps.dev/)** / Google Open Source Insights (**CC-BY
  4.0**) — the npm dependency graph. The derived edge lists shipped in
  `data/slice/` are built from this data; this notice is the required
  attribution for them.
- **[OSV.dev](https://osv.dev/)** — advisory records for the incident.
  Aggregated advisory data; its GitHub Advisory Database source is CC-BY 4.0.
- **[OpenSSF malicious-packages](https://github.com/ossf/malicious-packages)**
  (Apache-2.0) — the campaign roster of known malicious packages
  (`data/incident/campaign.json` derives from it).
- **[npm registry](https://registry.npmjs.org/)** — publish timestamps,
  version histories, and maintainer records, per npm's public registry terms.

**Libraries**

- Python: [FastAPI](https://fastapi.tiangolo.com/),
  [uvicorn](https://www.uvicorn.org/), [httpx](https://www.python-httpx.org/),
  the [neo4j Python driver](https://github.com/neo4j/neo4j-python-driver)
  (used as a Bolt client), [numpy](https://numpy.org/),
  [rapidfuzz](https://github.com/rapidfuzz/RapidFuzz),
  [xxhash](https://github.com/ifduyue/python-xxhash).
- Web: [React](https://react.dev/),
  [d3-force](https://github.com/d3/d3-force),
  [react-force-graph-2d](https://github.com/vasturiano/react-force-graph);
  built with [Vite](https://vitejs.dev/),
  [Tailwind CSS](https://tailwindcss.com/), and TypeScript.

**Tooling**

- Built with [Claude Code](https://claude.com/claude-code).

## License

MIT — see [LICENSE](LICENSE).
