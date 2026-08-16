# Fixture graph

A 30-node miniature of the real incident, small enough that every answer can be
worked out by hand and checked by reading. `graph.json` is the graph,
`expected_answers.json` is what the queries must return, and
`tests/test_fixture_live.py` asserts HydraDB agrees.

Two packages are compromised — `flux-core@1.0.1` and `left-pack@4.4.4` — both
published inside a six-minute live window, mirroring the shape of the May 11,
2026 event. Timestamps are ISO strings in `graph.json` and are converted to
epoch integers by the loader, so the window cannot drift from the times written
down.

## Package dependency edges

Arrows point from dependent to dependency, which is how `PKG_DEPENDS_ON` is
stored. `PKG_DEPENDED_BY` is the same set reversed, materialised by the loader
because HydraDB only traverses variable-length patterns outward from a fixed
source.

```
  dashboard ──► app-kit ──► flux-router ──┐
                   │                      ├──► flux-core   (compromised)
                   └──► flux-query ───────┘         ▲
                                  ▲                 │
  data-grid ──────────────────────┘   test-harness ─┘  (dev-only dependency)
      │
      └──────► left-pack   (compromised)

  standalone ──► orm-lite          flux-coree   (typosquat of flux-core)
```

`app-kit` reaches `flux-core` two ways (through `flux-router` and through
`flux-query`), so it is the diamond that proves the closure deduplicates.
`dashboard` is three hops out. `orm-lite`, `standalone` and `flux-coree` are
deliberately disconnected from the incident so a query that returns them is
wrong.

## The expected answers, and where they come from

**Blast radius of `flux-core`** — read the arrows backwards:

| Depth | New at this depth | Cumulative | Count |
|---|---|---|---|
| 1 | flux-router, flux-query, test-harness | same | 3 |
| 2 | app-kit (via both routes, counted once), data-grid | + 2 | 5 |
| 3 | dashboard | + 1 | 6 |
| 4 | nothing further | unchanged | 6 |

Minimum depths: flux-router, flux-query, test-harness = 1; app-kit, data-grid = 2;
dashboard = 3.

**Multi-source radius** seeded with both compromised packages: `data-grid`
arrives at depth 1 through `left-pack` instead of depth 2 through `flux-query`,
so depth 1 has 4 packages, depth 2 has 5, depth 3 has 6. `data-grid` is
reachable from both seeds and must still be counted once.

**Version-seeded traversal returns nothing.** Compromise is published per
version, but package-level dependency edges connect packages, so seeding a
traversal with `ver:npm/flux-core@1.0.1` finds no paths. The incident query
therefore seeds with package keys and uses the version nodes for the time
window.

**Maintainer overlap** — `alice` maintains flux-core, flux-router and orm-lite,
so the packages sharing a maintainer with flux-core are flux-router and
orm-lite. Note orm-lite is *not* in the blast radius: it shares an owner, not a
dependency. That contrast is the point of the query. `bob` maintains left-pack
and data-grid.

**Typosquats** — `flux-coree` is one edit from `flux-core`. Autocomplete on
`flux-` returns four names.

## Lockfiles

Three `package-lock.json` v3 files in `lockfiles/`, differing only in which
`flux-core` version they resolved:

| File | Service | Resolved | Installed | Verdict |
|---|---|---|---|---|
| `lock-inside-window.json` | svc-alpha | flux-core@1.0.1 | 19:23 UTC, inside the window | **EXPOSED** |
| `lock-before-window.json` | svc-beta | flux-core@1.0.0 | the previous day | CLEAN |
| `lock-after-fix.json` | svc-gamma | flux-core@1.0.2 | the next day | CLEAN |

In all three, the `flux-router` entry is AT_RISK: the version itself is clean,
but the package depends on a compromised package. A lockfile carries no
timestamp of its own, so the install time is supplied alongside it —
`lockfiles/expected.json` records the value each case assumes.
