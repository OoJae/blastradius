# Graph schema (v1)

Five node labels, eight relationship types. Every design choice below that
looks unusual is a response to a measured HydraDB constraint; the constraints
themselves are recorded in [VERIFICATION.md](VERIFICATION.md).

## Identity

HydraDB resolves nodes by a non-negative integer `id` property, and that
property is the identity `MERGE` and `MATCH` match on. Our entities are named
by strings, so every natural key is hashed:

```
id  = xxhash64(natural_key) & 0x7FFF_FFFF_FFFF_FFFF
key = the natural key, stored as a string property
```

`ingest/ids.py` is the only implementation, imported by both the loader and the
API, so an id computed at ingest time and an id computed at query time cannot
disagree. Keeping `key` on the node matters beyond readability: `algo.MSpaths`
seeds a traversal by property value (`sourceProperty: 'key'`), so the string
key is what the multi-source incident query looks up.

Relationships need ids too — HydraDB rejects a batched relationship `MERGE`
that does not carry `id: row.<field>`. An edge id is the hash of
`<TYPE>:<src_key>-><dst_key>`, which makes re-running the loader idempotent
rather than duplicating every edge.

## Nodes

| Label | Natural key | Properties |
|---|---|---|
| `:Package` | `pkg:npm/<name>` | `key`, `name`, `weekly_downloads`, `is_popular` |
| `:Version` | `ver:npm/<name>@<version>` | `key`, `name`, `version`, `published_at`, `compromised` |
| `:Maintainer` | `mnt:npm/<username>` | `key`, `username` |
| `:Advisory` | `adv:<GHSA or OSV id>` | `key`, `cve`, `severity`, `summary`, `published_at` |
| `:Service` | `lock:<sha256>` | `key`, `label`, `uploaded_at` |

Property values may only be integers, floats, booleans or strings, so every
timestamp is an epoch integer. The loader converts from ISO strings at ingest
(`ingest/load.py::epoch`) rather than trusting a hand-computed constant.

## Relationships

| Type | Pattern | Properties |
|---|---|---|
| `VERSION_OF` | `(:Version)→(:Package)` | — |
| `DEPENDS_ON` | `(:Version)→(:Package)` | `requirement`, `kind` |
| `PKG_DEPENDS_ON` | `(:Package)→(:Package)` | `via_versions` |
| `PKG_DEPENDED_BY` | `(:Package)→(:Package)` | `via_versions` |
| `MAINTAINS` | `(:Maintainer)→(:Package)` | — |
| `AFFECTS` | `(:Advisory)→(:Version)` | `live_from`, `live_until` |
| `SIMILAR_NAME` | `(:Package)→(:Package)` | `distance` |
| `RESOLVES` | `(:Service)→(:Version)` | — |

## Why the schema looks like this

**A package-level projection exists because a pattern carries one relationship
type.** The precise dependency data is version-level: a *version* declares a
dependency on a *package*. Walking that natively would alternate `DEPENDS_ON`
and `VERSION_OF`, which a single variable-length pattern cannot express. So the
loader materialises `PKG_DEPENDS_ON` (Package → Package), with `via_versions`
recording how many versions justified the edge. Version-level edges are kept
for precision; the projection is what traversals run on.

**Both directions are materialised because variable-length traversal must start
from a fixed source.** HydraDB rejects
`MATCH (d)-[:PKG_DEPENDS_ON*1..6]->(c {id: $x})` with *variable-length MATCH
requires a fixed source id*, and reversing the arrow in the pattern is rejected
identically. A blast radius is exactly the destination-anchored question — who
depends on me — so `PKG_DEPENDED_BY` stores the reverse projection and the
radius becomes an outgoing walk from the seed. This doubles the projection edge
count and is the single largest schema cost; it is what makes the product's
core query expressible at all.

**Advisory ranges become concrete edges with a time window.** OSV describes
affected versions as semver ranges, which cannot be stored as a property or
evaluated in a query — there are no functions in this Cypher subset. Semver is
resolved in Python at ingest, producing one `AFFECTS` edge per concrete
version, carrying `live_from` and `live_until` as epoch integers. "Were we
exposed during those six minutes" then reduces to an integer comparison on an
edge property, which is the temporal question the product is built around.

**Maintainer edges are separate from dependency edges on purpose.** The worm
propagated through stolen maintainer credentials, not through dependencies, so
"packages sharing a maintainer" is a different question from "packages that
depend on this" and must not be conflated. In the fixture, `orm-lite` shares an
owner with `flux-core` but is absent from its blast radius — the two queries
disagreeing is the correct behaviour.
