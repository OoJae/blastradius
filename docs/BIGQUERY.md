# Pulling the dependency graph from deps.dev on BigQuery

The npm dependency graph comes from Google's Open Source Insights dataset,
`bigquery-public-data.deps_dev_v1`. It is the only source that gives us
*dependents* in bulk — the deps.dev REST API exposes a dependent **count** but
not the list, which is the wrong shape for a blast radius.

The dataset is enormous and the naive query is genuinely expensive, so the
patterns below were measured before writing any extraction code.

## Cost guardrails

BigQuery's free tier is 1 TiB of query data per month; beyond that it is about
$6.25 per TiB. Measured against this dataset on 2026-08-16:

| Query | Data scanned |
|---|---|
| `SELECT *` from `DependenciesLatest` where npm | **82 TB** (~$500) |
| `SELECT *` from `DependentsLatest` where npm | **85 TB** |
| `DependencyGraphEdgesLatest`, 3 columns, npm | **129 TB** |
| Same, but with `Name = 'react'` added | 124 GB estimated, **506 MB actually billed** |
| Whole npm slice of one partition, `COUNT(DISTINCT Name)` | **511 GB billed** |

Three things follow.

**Always set `--maximum_bytes_billed`.** It makes an over-budget query fail
instead of running, which turns a $500 mistake into an error message. Every
example below sets it.

**Never query the `…Latest` views directly.** They are views over
day-partitioned tables and a query against them does not prune by partition.
Query the base table with an explicit `SnapshotAt` range instead.

**Dry runs over-estimate heavily, because they do not model cluster pruning.**
The `react` query above was estimated at 124 GB and billed 506 MB, a 245×
difference. Use `--dry_run` to catch catastrophes, not to predict cost; use a
capped real run to find the true number.

## The query pattern that works

The tables are day-partitioned on `SnapshotAt` and clustered on
`(System, Name, Version)`. Both filters matter:

```sql
SELECT Name, Version, `To`.Name AS dependency
FROM `bigquery-public-data.deps_dev_v1.DependencyGraphEdges`
WHERE SnapshotAt >= TIMESTAMP("2026-08-10")   -- partition pruning
  AND SnapshotAt <  TIMESTAMP("2026-08-11")
  AND System = "NPM"                          -- first clustering column
  AND Name = "react"                          -- second clustering column
```

Notes that cost time to discover:

* `To` is a reserved word and must be written `` `To` ``.
* `SnapshotAt = TIMESTAMP("2026-08-10")` matches **nothing** — the column
  carries a time component, so a half-open range is required. An equality
  filter silently returns zero rows and bills zero bytes, which looks like
  "this package has no dependencies" rather than an error.
* Partition ids are discoverable for free from
  `INFORMATION_SCHEMA.PARTITIONS`; the latest as of 2026-08-16 is `20260810`.
* `DependenciesLatest` holds the fully *resolved transitive* closure for every
  version of every package, which is why it is petabyte-scale. We want
  `DependencyGraphEdges` (direct edges) and build the closure ourselves inside
  HydraDB — that is the product.

## Measuring a query's cost without paying for it

Set an absurdly low cap. The query is refused, nothing is billed, and the error
states the exact requirement:

```
$ bq query --maximum_bytes_billed=1000000 'SELECT …'
Query exceeded limit for bytes billed: 1000000. 797794070528 or higher required.
```

That number is the true cost, cluster pruning included — which `--dry_run`
does not model. Every figure in the table below was obtained this way, for
free.

## What the extraction actually costs

One partition (`2026-08-10`), npm only, minimum columns:

| Extract | Cost | Needed? |
|---|---|---|
| `DependencyGraphEdges`: Name, Version, `To`.Name | 937 GB | version-level precision |
| `DependencyGraphEdges`: Name, `To`.Name | **743 GB** | package-level edges — the traversal workhorse |
| `PackageVersions`: Name, Version | **3.6 GB** | every version node |
| `Dependents`: Name, Dependent.Name | 417 GB | **no** — see below |

**We do not need the `Dependents` table at all**, which is the single biggest
saving available. It is the reverse of the edges we already extract, and the
loader materialises `PKG_DEPENDED_BY` from the forward edges anyway. Buying
417 GB of the same information twice would be a waste of most of a month's
free tier.

Filtering the bulk extract to just our slice does not help. Cost scales with
the number of distinct names, not down to them: one package bills 506 MB, ten
bill 2,066 MB. Ten thousand would cost more than reading all of npm, because
scattered lookups defeat clustering.

## Extraction plan

Against a 1 TiB monthly free tier, per billing account:

1. **Package-level edges, 2 columns — 743 GB.** The one large query. Write it
   to a destination table, then export and download; do not iterate on it.
2. **Versions — 3.6 GB.** Cheap enough to re-run freely.
3. **Version-level detail for the incident packages only** — a filtered query
   per package at roughly 500 MB, a few hundred megabytes in total for the ~42
   compromised packages.

That is about **750 GB, or 73% of the monthly allowance**, leaving ~270 GB of
headroom for mistakes. Taking the 937 GB version-level extract instead would
reach 92% and leave almost none.

Develop every query with a single-package `Name` filter first, where a run
bills a few hundred megabytes. If a second bulk run turns out to be necessary,
it costs about $5 — worth paying rather than contorting the query. The
expensive mistake is the unfiltered query, not the repeat.
