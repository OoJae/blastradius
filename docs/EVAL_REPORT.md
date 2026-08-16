# Evaluation: Mini Shai-Hulud / TanStack, 2026-05-11

Measured against `demo-20260810` at commit `8426f0b` on 2026-08-16. Every number below is produced by the evaluation and rendered into this file; none is typed by hand.

## Result in one screen

- **Exposure at depth 3: 8,555 packages** would have installed a malicious artifact. HydraDB and an independent offline oracle agree exactly.
- Exactness: 3/3 depths exact, zero missing and zero extra.
- Latency: depth 1 answers in 3 ms median per seed; depth 3 in 112 ms.
- Live window **derived from registry timestamps**, not quoted from the advisory: 2026-05-11T19:20:39Z to 2026-05-11T19:26:19Z.
- Campaign victim recall is **0 of 119**, and §4 shows why before it shows the number: the worm did not travel through published dependencies.

## 1. Exposure enumeration — the question the product answers

Given the 42 compromised packages, which packages would have pulled one in? Computed by HydraDB as anchored reverse traversals, and independently in Python from the same edge file. They must agree exactly.

| Depth | HydraDB | Offline oracle | Missing | Extra | Verdict | Median ms/seed | Wall |
|---|---:|---:|---:|---:|---|---:|---:|
| 1 | 1,321 | 1,321 | 0 | 0 | exact | 3 | 0s |
| 2 | 5,828 | 5,828 | 0 | 0 | exact | 28 | 3s |
| 3 | 8,555 | 8,555 | 0 | 0 | exact | 112 | 13s |

These figures are a **lower bound**: they cover the 52,161 packages loaded, out of 11,113,757 in the deps.dev snapshot. A package outside the slice cannot appear in a closure computed over it.

## 2. The live window, derived rather than quoted

| Source | From | Until | Span |
|---|---|---|---:|
| npm registry publish times (`npm_registry_time`) | 2026-05-11T19:20:39Z | 2026-05-11T19:26:19Z | 340s |
| Advisory prose | 2026-05-11T19:20:00Z | 2026-05-11T19:26:00Z | 360s |

The advisory states its window only in prose. Deriving it from the registry's own timestamps for all 84 malicious versions puts the close 19 seconds later, so an install in that gap is exposed under the data and clean under the prose. The graph carries the derived values.

## 3. Campaign victim recall — the honest zero

### 3.1 Why the number is zero

Before reporting recall, here is the scan that explains it. Across 12,247,380 dependency edge rows, **no package in any victim organisation depends on any `@tanstack` package**:

| Check | Count |
|---|---:|
| edge rows scanned | 12,247,380 |
| tanstack packages appearing as a dependency | 137 |
| packages depending on a tanstack package | 2,722 |
| victim org packages appearing as a dependent | 81 |
| victim org packages depending on tanstack | 0 |

The middle rows are the sanity check: the same scan does see `@tanstack` packages being depended on, and does see victim-org packages depending on things. The zero is a property of the data, not a broken query.

The worm spread by stealing CI credentials — a developer installed a compromised package in some project, their tokens leaked, and the attacker published to packages they owned. Those packages never declared a dependency on TanStack, so no dependency traversal can reach them. **This measures the attack's causal structure, not the tool's quality.**

### 3.2 Denominators

| Stage | Count | Meaning |
|---|---:|---|
| Campaign records | 172 | matched by the shared origin hash |
| npm victims | 170 | the other 2 are PyPI, excluded |
| Present in slice | 161 | loaded as graph nodes |
| **Scorable** | **119** | **minus the 42 seeds, which are inputs** |

### 3.3 Recall by channel

| Channel | Found | Denominator | Recall | Packages searched | Status |
|---|---:|---:|---:|---:|---|
| dependency closure (depth 3) | 0 | 119 | 0.0% | 8,555 | measured |
| maintainer overlap | 0 | 119 | 0.0% | 48 | measured |
| name similarity | 0 | 119 | 0.0% | 0 | measured |
| union of all channels | 0 | 119 | 0.0% | - | measured |

The name-similarity channel is measurable — edges are loaded — and reaches nothing here. Every compromised package is under @tanstack, and the names closest to them are @tanstack siblings, which same-scope suppression removes as owner-published rather than squatted. So the channel is measurable and genuinely finds nothing here.

### 3.4 Why each victim was not found

One code per victim, first match wins, so the counts reconcile against the roster.

| Reason | Victims | Counted as a miss? |
|---|---:|---|
| `out_of_ecosystem` | 2 | no — PyPI, an npm graph cannot reach it |
| `is_seed` | 42 | no — a seed is an input, not a discovery |
| `not_in_snapshot` | 9 | yes — absent from the deps.dev snapshot |
| `no_path_in_slice` | 119 | yes — in the graph, no path reaches it |
| **total** | **172** | reconciles against the roster |

## 4. Anti-cheating guard

Every discovery query ran through a guard under the `discovery` policy, which refuses any query mentioning the advisory, the `compromised` flag, or a victim name as a literal. 214 queries were executed and are recorded verbatim in `data/eval/queries.jsonl`.

Without this the recall claim would be circular: the advisory listing the answer sits one hop from the seeds in the same graph. The guard is checked before the driver is touched, and `tests/test_eval_guard.py` plants the obvious shortcut and asserts it is refused.

## 5. What this evaluation does not prove

- It does not measure exposure for npm as a whole, only for the loaded subgraph of 52,161 packages.
- It does not prove the tool would have caught this campaign in real time. Nothing built on published dependency edges would have.
- It does not measure precision. There is no ground-truth list of packages that were genuinely harmed downstream, only of packages that were themselves compromised.
- The maintainer channel is bounded by registry coverage, so its recall is a floor rather than a ceiling.
- Closure correctness is established up to the sizes actually checked here; HydraDB's non-configurable intermediate-row budget is untested above them.

