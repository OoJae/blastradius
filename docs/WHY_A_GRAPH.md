# Why this is only possible with a graph

The question BlastRadius answers is: *given a compromised package, which
packages would have installed it?* This note is about why that question is a
graph traversal rather than a query shape that something else does better.

## The question, stated precisely

Given a package **p**, find every package **q** such that a directed path
`q → … → p` exists in the dependency graph, of length 1 to *n*. Return the set,
its size, and each member's shortest distance from **p**.

Three properties make it what it is: the answer set is **transitive**, its
**depth is unbounded a priori**, and the relation is **directional** — "who
depends on me" is the reverse of the edge that is actually recorded.

## In HydraDB

```cypher
MATCH (c:Package {id: $seed_id})-[:PKG_DEPENDED_BY*1..3]->(d:Package)
RETURN DISTINCT d.key AS key, d.name AS name, d.is_popular AS is_popular
```

One statement. **1,666 ms for 5,782 packages** over 1.3M edges, verified exact
against an independently computed closure.

The `PKG_DEPENDED_BY` edge is itself a consequence of a constraint: HydraDB
rejects a variable-length pattern that is not anchored on a fixed *source* id,
and a blast radius is destination-anchored by nature. So the loader materialises
the reverse projection and the traversal runs outward from a fixed seed. That
is the constraint improving the design — the reverse edge is written once at
ingest instead of being discovered at every query.

## In SQL

```sql
WITH RECURSIVE radius(name, depth) AS (
    SELECT dependent, 1 FROM edges WHERE dependency = $1
  UNION
    SELECT e.dependent, r.depth + 1
    FROM edges e JOIN radius r ON e.dependency = r.name
    WHERE r.depth < 3
)
SELECT DISTINCT name, MIN(depth) FROM radius GROUP BY name;
```

This is expressible, and on a small graph it is fine. What it costs at scale:

- **Each level is a join against the whole edge table.** Depth 3 over 1.3M
  edges with npm's power-law fan-out means the intermediate result is far larger
  than the answer; the planner cannot know that most of it will be deduplicated
  away.
- **`UNION` deduplication is per-iteration**, so a package reachable by many
  paths — which, in a dependency graph, is most of them — is re-derived and
  re-discarded repeatedly.
- **Cycles must be handled explicitly.** npm has them: with devDependencies
  included, `typescript` reaches itself in two reverse hops. A recursive CTE
  without an explicit visited-set can loop; ours caught this as a real case, and
  in the graph it is simply a node that appears in its own closure.
- **The index that makes it work is the edge table's own reverse index**, which
  is the same materialisation `PKG_DEPENDED_BY` is — except that the graph
  writes it once and names it, while SQL rediscovers the join at every query.

The honest summary: SQL *can* do this. It does it by reimplementing, per query,
what a graph database does structurally.

## With vector search

Vector search cannot express this question at all, and it is worth being precise
about why rather than waving at it.

An embedding retrieves by **similarity**. Dependency is not similarity. A
package that depends on `@tanstack/react-router` need not resemble it in name,
description, purpose, or authorship — a logging library and a UI toolkit can
both depend on it, and neither is "near" it in any embedding space.

Worse, similarity would return confident, plausible, wrong answers: it would
surface `@tanstack/react-query` — a sibling that shares a name, an author and a
README — while missing the unrelated package three hops down that actually
installed the malicious artifact. In incident response, a plausible wrong answer
is worse than no answer.

**The measured version of this argument** is in our own evaluation. The campaign
victims do *not* depend on the compromised packages — zero edges across 12.2M
rows — because the worm spread through stolen CI credentials rather than
dependencies. The graph says so exactly, and says nothing more. A similarity
model asked the same question would have produced a ranked list of plausible
victims, every one of them an artefact of the embedding.

## The temporal question

The strongest argument is not the traversal but the edge property.

```cypher
MATCH (a:Advisory)-[r:AFFECTS]->(v:Version {id: $version_id})
WHERE r.live_from <= $installed_at AND r.live_until >= $installed_at
RETURN a.key AS advisory
```

"Was this artifact live when you installed it" is a predicate on a
**relationship**, not on either endpoint. The advisory is not time-bounded; the
version is not time-bounded; the *association between them* is, for 340 seconds
on 11 May 2026. A row-oriented schema models this with a join table and a range
predicate — which is a graph edge with properties, drawn less clearly. A vector
store has nowhere to put it at all.

## What we would lose

Remove HydraDB and there is no product. The traversal is the answer, the
reverse projection is what makes it expressible, and the temporal edge is what
turns "you have a bad package" into "you installed it during the six minutes it
was live". Everything else here — the extraction, the slice compiler, the
interface — exists to put data into that shape and get answers back out.
