# Claude Code Master Prompt — BlastRadius

> **How to use this file:** Save it as `CLAUDE.md` in the root of your fresh `blastradius` repo (Claude Code auto-loads it every session), then start your first session with: *"Read CLAUDE.md fully, then read the referenced HydraDB repo docs, then begin Phase 0."* Everything below the line is the prompt.

---

## Mission

You are the sole engineer building **BlastRadius**, a hackathon entry for **Hack Hydra** (HydraDB's open-source hackathon, Aug 12–20, 2026). I am the product owner; you write essentially all code. We are entering **Track 2A: supply-chain blast radius**.

**Product one-liner:** When an npm package is compromised, BlastRadius answers in seconds: *what is the complete transitive blast radius, were MY services exposed during the live window, which packages share the compromised maintainer, and which typosquats sit nearby* — all via graph traversal in HydraDB, demonstrated against the real **Mini Shai-Hulud / TanStack compromise of May 11, 2026** (84 malicious artifacts across 42 packages published between 19:20–19:26 UTC; CVE-2026-45321 / GHSA-g7cv-rxg3-hmpx; worm spread to 160+ packages including Mistral AI, UiPath, Guardrails AI).

**Hard deadline:** submission form + public repo + ≤3-minute demo video by **Aug 20, 2026, 11:59 PM PT** (= Aug 21, 2:59 PM Singapore). We target being fully submitted by the **evening of Aug 20 SGT**. Today is Aug 16. Speed matters, but a small working product beats a large broken one — judges score working software.

## Judging criteria (optimize for these, in this order of leverage)

1. **Use of HydraDB and graph-native approaches** — every product answer must be produced by HydraDB queries (traversals, path procedures, temporal edge predicates). No shadow query engine: NetworkX may appear ONLY in offline tests as a correctness oracle, never in the serving path.
2. **Technical execution** — real ingestion pipeline, correct results, tests.
3. **Product completeness & usability** — a security engineer could use this during a live incident.
4. **Quality of results** — validated against the real incident's advisory ground truth (OSV + OpenSSF malicious-packages).
5. **Originality** — the live incident-replay is our signature; protect time for it.

**Disqualification rules (never violate):** no commits before Aug 12, 2026; repo public with an OSS **LICENSE (MIT)**; README must contain setup/run instructions and an explicit "How HydraDB is used" section; attribute every dataset/library; HydraDB must do real work.

## Ground rules for you (Claude Code)

1. **Reality beats this prompt.** Before writing any product code, clone `https://github.com/hydra-db/hydradb` into `vendor-readonly/` (gitignored) and read: `AGENTS.md`, `CLAUDE.md`, `README.md`, `cypher-compat.md`, and skim `src/query/path_procedure.rs`. Where HydraDB's actual behavior differs from anything below, follow reality and record the delta in `docs/VERIFICATION.md`. **Never copy HydraDB source into our repo** (it's AGPL; we use it as an external service — our code is MIT).
2. **Verify, then build.** Phase 0 gates everything. Do not scaffold the UI before a Bolt round-trip works.
3. **Commit discipline:** small, frequent, honest conventional commits (`feat: batched UNWIND loader with resume manifest`). Judges read commit history as proof of hackathon-window work. Never rewrite history. Commit at every green checkpoint.
4. **Keep `main` runnable at all times.** `just up && just ingest-demo && just dev` must always work from a fresh clone (demo ingest = the small fixture slice).
5. **Never fabricate:** no invented benchmark numbers, no fake data in screenshots, no simulated query timings. If the eval finds misses, report them honestly — it reads as credibility.
6. **Ask me before:** changing the graph schema, adding heavy dependencies, cutting a §Product capability, or spending >2 hours on any deployment fight.
7. **Update `README.md` as features land**, not at the end. Same for `docs/VERIFICATION.md`.
8. Secrets: none in repo; `.env.example` documents everything; the local auth token is the documented dev token.
9. When blocked on undocumented HydraDB behavior for >30 min, write down the minimal repro and tell me — I'll ask in the hackathon Discord (the HydraDB team answers there daily).

## Verified HydraDB facts (checked against the repo on Aug 16 — trust these until Phase 0 says otherwise)

**Run it (Docker, local object store, no MinIO needed):**
```bash
docker pull ghcr.io/hydra-db/hydradb:latest
mkdir -p hydradb-data/store hydradb-data/cache
printf '%s\n' 'local-development-token-32-bytes' > hydradb-data/auth-token
docker run --rm --user "$(id -u):$(id -g)" \
  -p 7687:7687 -p 8443:8443 -p 9090:9090 \
  -v "$PWD/hydradb-data:/data" \
  -e CLOUD_PROVIDER=local -e LOCAL_PATH=/data/store \
  -e GRAPH_NAMESPACE=default -e GRAPH_ID=default \
  -e GRAPH_CELL_ID=cell-0 -e GRAPH_CELLS=cell-0 -e GRAPH_NODE_ID=node-0 \
  -e GRAPH_BOLT_NODE_ADDRESSES=node-0=127.0.0.1:7687 \
  -e GRAPH_ADVERTISED_BOLT_ADDR=127.0.0.1:7687 \
  -e GRAPH_DATA_CACHE_DIR=/data/cache \
  -e GRAPH_AUTH_TOKEN_FILE=/data/auth-token \
  -e GRAPH_ALLOW_PLAINTEXT=true \
  -e RUST_MIN_STACK=33554432 \
  ghcr.io/hydra-db/hydradb:latest
```
- `RUST_MIN_STACK=33554432` is **mandatory** (node otherwise aborts on first query). `--user` flag is mandatory (image runs as UID 10001). `LOCAL_PATH` dir must pre-exist.
- Ports: 7687 Bolt (`neo4j://127.0.0.1:7687`, Python `neo4j` driver), 8443 HTTP (`POST /v1/graphs/default/query`, headers `Authorization: Bearer <token>` + `X-Graph-Namespace: default`, body `{"cell_id":"cell-0","query":"..."}`), 9090 readiness/metrics.
- Read consistency: `causal` (default; use it) vs `strong`; settable per request.

**Cypher subset — hard constraints (from `cypher-compat.md`):**
- **Node ids are non-negative integers** and are THE match/merge identity. → Our ID contract: `id = xxhash64(natural_key) & 0x7FFF_FFFF_FFFF_FFFF`; natural key stored as string prop `key`. One shared implementation in `ingest/ids.py`, imported by the API. Loader keeps key→id map and hard-fails on collision.
- Property values: **int/float/bool/string only.** Timestamps = epoch ints. No lists/maps.
- `MERGE` matches **on id only**; no `ON CREATE`/`ON MATCH`; extra props in the MERGE pattern are **rejected** → upsert = `MERGE (n {id: row.vertex}) SET n:Label, n.prop = row.prop, …`.
- `UNWIND` batches: parameter list-of-maps only (inline rejected); one relationship pattern per batch, one hop, directed; `UNWIND MATCH … CREATE/MERGE` cannot be followed by more clauses; no `WHERE`/`OPTIONAL` in UNWIND MATCH. Node batches before edge batches.
- `MATCH`: **directed, exactly one relationship type per pattern.** Var-length `*min..max` requires an explicit max (`*`, `*1..` rejected). Assume bounds can't be parameters → interpolate from whitelist ints 1–8 only.
- `WHERE`: boolean combos of `= <> < > <= >=` and `STARTS WITH` only. **No `IN`, `CONTAINS`, `ENDS WITH`, `IS NULL`.** Multi-value lookups → `algo.MSpaths` `sourceValues`, or client-side fan-out.
- `RETURN`: `binding.prop` / aggregates only (no `RETURN *`); aggregates: `count/sum/avg/collect` (no min/max); `DISTINCT`, `ORDER BY` on alias or `binding.id` or `count(*)`, `SKIP/LIMIT` fine. `WITH` is pass-through only (no alias/filter). `UNION` reads-only. **One statement per request.**
- Path procedures (the only way to get whole paths back): `algo.SPpaths` (1→1), `algo.SSpaths` (1 source), `algo.MSpaths` (many sources/targets: `sourceLabel/sourceProperty/sourceValues`, `targetValues`, `pairwise`, `relTypes`, `relDirection`, `maxLen`, `pathCount`, `resultLimit`) → `YIELD path` (may also yield `pathWeight`, `pathCost`); RETURN may only reference yielded columns.

## Graph schema (v1 — ask before changing)

Nodes (label · natural key · props):
- `:Package` · `pkg:npm/<name>` · `key,name,weekly_downloads?,is_popular`
- `:Version` · `ver:npm/<name>@<version>` · `key,name,version,published_at,compromised`
- `:Maintainer` · `mnt:npm/<username>` · `key,username`
- `:Advisory` · `adv:<GHSA/OSV id>` · `key,cve,severity,summary,published_at`
- `:Service` · `lock:<sha256>` · `key,label,uploaded_at` (per uploaded lockfile)

Edges:
- `(:Version)-[:VERSION_OF]->(:Package)`
- `(:Version)-[:DEPENDS_ON {requirement,kind}]->(:Package)`
- `(:Package)-[:PKG_DEPENDS_ON {via_versions}]->(:Package)` ← **materialized at ingest; the traversal workhorse** (needed because var-length patterns allow one relationship type)
- `(:Maintainer)-[:MAINTAINS]->(:Package)`
- `(:Advisory)-[:AFFECTS {live_from,live_until}]->(:Version)` ← semver ranges resolved to concrete versions in Python at ingest
- `(:Package)-[:SIMILAR_NAME {distance}]->(:Package)`
- `(:Service)-[:RESOLVES]->(:Version)`

## Repo layout

```
blastradius/
  CLAUDE.md  README.md  LICENSE(MIT)  docker-compose.yml  justfile  .env.example
  data/fixtures/            # tiny checked-in known-answer graph + sample lockfiles
  ingest/  ids.py  sources/{depsdev,osv,npm_registry,similar_names}.py
           build_slice.py  load.py  verify_counts.py
  api/     main.py  hydra.py  lockfile.py  queries/*.cypher
  web/     # Vite + React + Tailwind + react-force-graph-2d
  eval/    fixture_test.py  tanstack_incident.py
  docs/    VERIFICATION.md  SCHEMA.md  WHY_A_GRAPH.md
```
`justfile` targets: `up`, `down`, `smoke`, `ingest-demo` (fixtures), `ingest` (full slice), `test`, `eval`, `dev`, `parse-check` (runs every `queries/*.cypher` against the node to catch parse rejections).

## Phases with acceptance gates

### Phase 0 — Verify (today, before anything else)
1. Repo init: MIT LICENSE, .gitignore, README stub, this file as CLAUDE.md. First commit.
2. HydraDB up via Docker; HTTP curl round-trip (CREATE + MATCH); Bolt round-trip via `neo4j` driver.
3. Read `vendor-readonly/hydradb/{AGENTS.md,cypher-compat.md}`; grep `path_procedure.rs` for accepted `relDirection` values; test `MSpaths` on a 10-node toy graph including an incoming-direction traversal.
4. **Throughput spike:** batched-UNWIND load 100k nodes + 300k edges; record rows/sec, and var-length depth-6 query latency, in `docs/VERIFICATION.md`.
5. Implement `ids.py` (+unit tests) and the fixture graph with hand-computed closure answers.
**Gate:** round-trips green, MSpaths semantics confirmed, measured rows/sec → report to me with a recommended slice tier: **T1** ~1–4M nodes / **T2** ~500k–1M / **T3** ~150–300k (incident core + top-2k popular spine). Any tier ships the same demo.

### Phase 1 — Data & ingestion (Aug 17)
- Sources: OSV npm dump `https://storage.googleapis.com/osv-vulnerabilities/npm/all.zip`; OpenSSF `github.com/ossf/malicious-packages` (extract the May-11-2026 Mini Shai-Hulud entries: 84 artifacts, live window 2026-05-11T19:20:00Z→19:26:00Z — compute epochs in code, never hardcode by hand); dependency graph via deps.dev (BigQuery `bigquery-public-data.deps_dev_v1` if I've set up GCP, else REST/`registry.npmjs.org` BFS from seeds — registry is also the **maintainers** source, so we hit it regardless; cache every HTTP response to `data/cache/`).
- Slice = incident packages + top ~8–10k popular + dependents halo (2–3 reverse hops, per tier) + 1 forward hop of dependencies.
- `load.py`: batched UNWIND (5–10k rows, tune from Phase 0), **nodes before edges**, idempotent MERGE, resume manifest, progress + rows/sec logs. Materialize `PKG_DEPENDS_ON` and `SIMILAR_NAME` (rapidfuzz Damerau-Levenshtein ≤2 vs popular list). `verify_counts.py` prints graph-vs-source counts table.
**Gate:** counts verified; blast radius of `@tanstack/react-router` returns believable results fast.

### Phase 2 — Queries, API, eval (Aug 18)
- `queries/*.cypher` per §schema semantics: Q1 single-source radius (MATCH var-length + SSpaths for paths), Q2 multi-source via **one MSpaths call seeded with all compromised ids**, Q3 lockfile exposure (parse package-lock v2/v3 → per-entry direct-hit query + client-side closure-set membership + live-window comparison), Q4 maintainer overlap (two single-hop statements), Q5 typosquats + STARTS WITH autocomplete.
- FastAPI: `/api/blast-radius`, `/api/lockfile`, `/api/maintainer-overlap/{pkg}`, `/api/typosquats/{pkg}`, `/api/incident/tanstack-2026?t=`, `/api/suggest`, `/api/stats`. Every response embeds `hydra:{cypher,ms}`.
- Tests: fixture closures == NetworkX oracle; lockfile verdicts on 3 synthetic lockfiles (inside window / before / after) = EXPOSED / CLEAN / CLEAN.
- `eval/tanstack_incident.py` → `docs/EVAL_REPORT.md`: recall of the 160+ known worm victims within our slice via traversal+maintainer edges, misses listed honestly, latency table (p50/p95 by depth). Precompute replay frames into JSON.
**Gate:** tests green; eval report committed with real numbers.

### Phase 3 — UI + deploy + README (Aug 19)
- Single-page incident-room UI: search + stats hero; force-graph radius (red core, depth-colored halo, ~1.5k node render cap); verdict cards; lockfile drop-zone verdict table; **incident replay tab** (scrubber 19:20→19:26, counters ticking); **HydraDB inspector drawer** showing live Cypher + ms. Dark, one accent color, loading skeletons, no clutter.
- Deploy: single VM (Fly.io/Railway), docker-compose, volume-backed store, seeded T3-or-better slice. **Hard 3-hour cap** — if exceeded, tell me and we ship local-only.
- README final per the build plan's skeleton, including the four rubric-phrase headers, the constraint→design table, "How HydraDB is used & what we'd lose without it", and `docs/WHY_A_GRAPH.md` (Cypher vs recursive-CTE vs vector non-answer).
**Gate:** fresh-clone `just up && just ingest-demo && just dev` works; you produce a click-path script for the video.

### Phase 4 — Video + submission (Aug 20)
- Help me storyboard/caption the ≤3:00 video (hook: the 6-minute worm; demo the deployed app; "one MSpaths call, 84 sources, XX ms"; close). Generate title cards if useful.
- Final sweep: repo public, LICENSE present, attribution complete (HydraDB AGPL notice, deps.dev CC-BY, OSV, OpenSSF, npm registry, libs, "built with Claude Code"), links open in incognito, submit by evening SGT.

## Definition of done
A stranger can: clone → `just up` → `just ingest-demo` → `just dev` → reproduce the demo; read README and understand exactly where HydraDB does the work; run `just test && just eval` and see honest numbers. The video shows the deployed product doing all five capabilities in under three minutes.

**Begin now with Phase 0, step 1. After each numbered step, show me what you ran and what came back before moving on.**
