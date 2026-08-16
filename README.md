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

_TODO (Phase 3)._ Measured facts gathered so far live in
[docs/VERIFICATION.md](docs/VERIFICATION.md).

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
