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

## Extraction plan

A full npm edge extract for one partition is roughly 0.9 TB, which fits inside
one month's free tier but leaves no room to iterate. So:

1. Develop and validate every query with a single-package `Name` filter, where
   each run bills a few hundred megabytes.
2. Run the bulk extract **once**, writing to a destination table rather than
   streaming results, then export that table to GCS and download it.
3. If a second bulk run is needed it costs about $6, which is worth paying
   rather than contorting the query — the expensive mistake is the unfiltered
   query, not the repeat.
