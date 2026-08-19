"""The BlastRadius HTTP service.

Every analytical answer here is a live HydraDB query, and every response carries
the statement that produced it along with how long it took. Two answers are
served from memory -- autocomplete and the header counts -- because their
graph-native forms measure seconds rather than milliseconds; both are captured
from HydraDB at startup, both say so in the response, and `?live=1` on suggest
runs the real query so the two can be compared.
"""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from api import lockfile as lockfile_mod
from api import timeline
from api import forecast as forecast_mod
from api.closure import ClosureCache
from api.digest import INCIDENT_ADVISORY, GraphDigest
from api.hydra import HydraClient, NotComputed
from api.queries import ALLOWED_DEPTHS, seed_values
from api.trace import Trace
from ingest.ids import adv_key, hash_key, pkg_key, ver_key
from ingest.load_slice import load as load_slice
from ingest.sources.live_window import iso

DEFAULT_DEPTH = 3
state: dict[str, Any] = {}


SLICE_DIR = Path(__file__).resolve().parent.parent / "data" / "slice"

# A package with dependents in any real slice, used only to tell a loaded graph
# from one whose load was interrupted after the nodes.
PROBE_PACKAGE = "@tanstack/react-router"

# The fixture graph's hub. Its dependents are the fixture's known answers, so
# rows here mean the fixture edges landed -- a graph someone loaded on purpose
# with `just ingest-demo`, not the debris of an interrupted slice load.
FIXTURE_PROBE = "flux-core"


def load_decision(packages: int, probe_rows: int, fixture_rows: int) -> str:
    """What a boot should do, given what the probes found.

    Pure so the contract is pinned by an offline test: the previous version of
    this logic treated a deliberately-loaded fixture graph as an interrupted
    slice load and silently rebuilt 52k packages on top of it, which is exactly
    the kind of decision that deserves a table:

      no packages at all          -> load
      real-slice probe has edges  -> serve
      fixture probe has edges     -> serve-fixture (never rebuild over it)
      nodes but no edges anywhere -> load (an interrupted load; MERGE resumes)
    """
    if not packages:
        return "load"
    if probe_rows:
        return "serve"
    if fixture_rows:
        return "serve-fixture"
    return "load"


async def load_if_empty(client: HydraClient) -> bool:
    """Build the graph on first boot, if this deployment has an empty one.

    A fresh deployment ships the slice as 9.1 MB of CSV rather than 1.7 GB of
    object store, so the graph has to be built once on the target. It cannot be
    a build step -- Railway's private network only exists at runtime, so the
    database is unreachable while the image is being built.

    Every write is an idempotent MERGE, so a restart part-way through resumes
    rather than duplicating or corrupting.
    """
    trace = Trace()
    rows = await client.run_spec("q8_label_count", trace=trace, interpolate={"label": "Package"})
    packages = rows[0]["n"] if rows else 0

    # Nodes alone do not mean the graph is loaded. Nodes are written first and
    # the edges take far longer, so an interrupted load leaves a graph that
    # looks populated and answers every blast radius with zero. Probe an
    # anchored traversal as well -- if a package has no dependents at all, the
    # edges never landed and the load has to run again. MERGE makes that safe.
    probe_rows = 0
    fixture_rows = 0
    if packages:
        probe = await client.run_spec(
            "q1_radius_nodes",
            trace=trace,
            interpolate={"depth": 1},
            seed_id=hash_key(pkg_key(PROBE_PACKAGE)),
        )
        probe_rows = len(probe)
        if not probe_rows:
            fixture = await client.run_spec(
                "q1_radius_nodes",
                trace=trace,
                interpolate={"depth": 1},
                seed_id=hash_key(pkg_key(FIXTURE_PROBE)),
            )
            fixture_rows = len(fixture)

    decision = load_decision(packages, probe_rows, fixture_rows)
    if decision == "serve":
        return False
    if decision == "serve-fixture":
        print(
            "fixture graph detected -- serving it as loaded "
            "(run `just ingest` to load the real slice instead)",
            flush=True,
        )
        return False
    if packages:
        print(
            f"{packages:,} packages but no dependents for {PROBE_PACKAGE} "
            "-- a previous load was interrupted; loading again",
            flush=True,
        )

    # Which slice this deployment builds. The hosted instance runs a smaller
    # one than local development: its object store has to fit a 5 GB volume,
    # and the store peaks well above its settled size during compaction.
    wanted = os.environ.get("BLASTRADIUS_SLICE")
    candidates = sorted(p for p in SLICE_DIR.glob("*") if p.is_dir())
    if wanted:
        candidates = [p for p in candidates if p.name == wanted] or candidates
    if not candidates:
        print("graph is empty and no slice is bundled; serving an empty graph", flush=True)
        return False

    slice_dir = candidates[0]
    print(f"graph is empty -- loading {slice_dir.name} (this takes ~25 minutes)", flush=True)
    state["loading"] = slice_dir.name
    try:
        await run_in_threadpool(load_slice, slice_dir)
        print(f"loaded {slice_dir.name}", flush=True)
    finally:
        state.pop("loading", None)
    return True


async def prepare(client: HydraClient) -> None:
    """Load if needed, then build the digest, then warm the incident closures.

    This runs as a background task rather than blocking startup. A fresh
    deployment spends ~25 minutes building its graph, and a platform
    healthcheck will not wait that long -- so the server accepts connections
    immediately and reports its own progress through /api/health and
    /api/stats, which is also more useful to a human than a dead port.
    """
    try:
        await load_if_empty(client)

        print("building the graph digest...", flush=True)
        started = time.perf_counter()
        digest = await GraphDigest.build(client)
        state["digest"] = digest
        print(
            f"digest ready in {time.perf_counter() - started:.1f}s: "
            f"{len(digest.names):,} packages, advisory {digest.advisory_key or 'none'}",
            flush=True,
        )

        if digest.incident_packages:
            await state["closures"].warm(digest.incident_packages, DEFAULT_DEPTH)
            print("building the next-wave forecast...", flush=True)
            state["forecast"] = await forecast_mod.build(
                client, digest, state["closures"], DEFAULT_DEPTH
            )
            fc = state["forecast"]
            if fc is not None:
                print(
                    f"forecast ready: {fc.payload['reach']['packages']} candidates, "
                    f"hindsight {fc.payload['hindsight']['flagged']}"
                    f"/{fc.payload['hindsight']['fell_later']}",
                    flush=True,
                )
    except Exception as exc:  # noqa: BLE001 - surfaced through /api/health
        state["startup_error"] = str(exc).splitlines()[0][:300]
        print(f"startup failed: {state['startup_error']}", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = HydraClient.open()
    state["client"] = client
    state["closures"] = ClosureCache(client)
    state["prepare"] = asyncio.create_task(prepare(client))
    try:
        yield
    finally:
        task = state.get("prepare")
        if task is not None:
            task.cancel()
        client.close()


app = FastAPI(title="BlastRadius", version="0.1.0", lifespan=lifespan)


def ok(result: Any, trace: Trace, primary: str | None = None) -> dict[str, Any]:
    return {"status": "ok", "result": result, "hydra": trace.envelope(primary)}


@app.exception_handler(NotComputed)
async def not_computed_handler(request: Request, exc: NotComputed) -> JSONResponse:
    """A failure keeps the shape of an answer, so the UI has one code path."""
    return JSONResponse(
        status_code=503,
        content={
            "status": "not_computed",
            "result": None,
            "not_computed": exc.as_dict(),
            "hydra": {"cypher": exc.cypher, "ms": round(exc.ms, 1), "steps": []},
        },
    )


def digest() -> GraphDigest:
    return state["digest"]


def starting_response() -> JSONResponse | None:
    """The answer for the window before the digest exists.

    The server accepts connections while the background prepare() task builds
    the graph and its digest -- that is the whole point of prepare() being a
    background task -- so every analytical endpoint has to have an answer for
    "not yet". This is that answer, in the same not_computed envelope the
    interface already renders as a retryable card, rather than the bare 500 a
    KeyError on state["digest"] used to produce.
    """
    if "digest" in state:
        return None
    if state.get("startup_error"):
        hint = f"startup failed: {state['startup_error']}"
        retryable = False
    elif state.get("loading"):
        hint = (
            f"loading the {state['loading']} slice -- a fresh deployment "
            "builds its graph once, in about 25 minutes"
        )
        retryable = True
    else:
        hint = "building the digest from HydraDB; this takes seconds on a loaded graph"
        retryable = True
    return JSONResponse(
        status_code=503,
        content={
            "status": "not_computed",
            "result": None,
            "not_computed": {
                "kind": "unavailable",
                "message": "the service is still starting",
                "hint": hint,
                "retryable": retryable,
            },
            "hydra": {"cypher": "", "ms": 0, "queries": 0, "live": 0, "cached": 0, "steps": []},
        },
    )


@app.get("/api/health")
async def health() -> dict[str, Any]:
    """Always answers, so a platform healthcheck passes while the graph builds."""
    ready = "digest" in state
    body: dict[str, Any] = {"status": "ok" if ready else "starting", "ready": ready}
    if not ready:
        # Say which slow thing is happening rather than leaving a caller to guess.
        if state.get("startup_error"):
            body["status"] = "error"
            body["error"] = state["startup_error"]
        elif state.get("loading"):
            body["stage"] = "loading the graph"
            body["slice"] = state["loading"]
            body["note"] = "a fresh deployment builds its graph once, in about 25 minutes"
        else:
            body["stage"] = "building the autocomplete index from HydraDB"
    return body


@app.get("/api/stats")
async def stats() -> dict[str, Any]:
    if "digest" not in state:
        return {
            "status": "starting",
            "result": None,
            "loading": state.get("loading"),
            "error": state.get("startup_error"),
        }
    d = digest()
    graph: dict[str, Any] = {}
    for label, value in d.counts.items():
        entry: dict[str, Any] = {"value": value, "source": "hydra"}
        if value is None:
            # A count we could not take is reported as such, with the server's
            # own words, rather than quietly replaced by a number from elsewhere.
            entry["error"] = d.count_errors.get(label)
            entry["source"] = "refused"
        graph[label.lower()] = entry

    return {
        "status": "ok",
        "result": {
            "graph": graph,
            "advisory": {
                "key": d.advisory_key,
                "is_the_demonstrated_incident": d.has_incident,
                "artifacts": len(d.artifacts),
                "packages": len(d.incident_packages),
                "window": d.window,
                "note": None
                if d.has_incident
                else f"this graph does not contain {INCIDENT_ADVISORY}",
            },
            "suggest": {
                "names": len(d.names),
                "memory_bytes": d.memory_bytes(),
                "source": "captured from HydraDB at startup",
                "why": (
                    "the graph-native prefix search measures about five seconds on "
                    "this data because there is no index DDL in this Cypher subset; "
                    "/api/suggest?live=1 runs it so the two can be compared"
                ),
            },
            "warming": state["closures"].warming,
            "loading": state.get("loading"),
            "built_at": d.built_at,
            "boot_queries": [step.as_step() for step in d.boot],
        },
    }


@app.get("/api/suggest")
async def suggest(
    q: str = Query(""),
    limit: int = Query(10, ge=1, le=50),
    live: bool = Query(False),
) -> Any:
    starting = starting_response()
    if starting is not None:
        return starting
    trace = Trace()
    if live:
        rows = await state["client"].run_spec("q11_prefix_search", trace=trace, prefix=q)
        result = [{"name": row["name"], "is_popular": None} for row in rows][:limit]
        return ok({"matches": result, "source": "live"}, trace)
    return ok({"matches": digest().suggest(q, limit), "source": "startup index"}, trace)


@app.get("/api/blast-radius")
async def blast_radius(
    pkg: str = Query(...),
    depth: int = Query(DEFAULT_DEPTH),
    limit: int = Query(1500, ge=1, le=20000),
    fresh: bool = Query(False),
) -> dict[str, Any]:
    if depth not in ALLOWED_DEPTHS:
        return JSONResponse(
            status_code=422,
            content={"status": "error", "message": f"depth must be one of {list(ALLOWED_DEPTHS)}"},
        )
    starting = starting_response()
    if starting is not None:
        return starting
    trace = Trace()
    found = await state["client"].run_spec(
        "q6_package_lookup", trace=trace, package_id=hash_key(pkg_key(pkg))
    )
    if not found:
        return JSONResponse(
            status_code=404,
            content={
                "status": "not_found",
                "result": None,
                "message": f"{pkg} is not in this graph",
                "hydra": trace.envelope(),
            },
        )

    try:
        closure = await state["closures"].radius(pkg, depth, trace=trace, fresh=fresh)
    except NotComputed as exc:
        # Rather than nothing, offer the deepest answer already computed, with
        # its age, so a slow depth degrades into a shallower fact.
        fallback = state["closures"].best_available(pkg, depth)
        if fallback is None:
            raise
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_computed",
                "result": None,
                "partial": {
                    "depth": fallback.depth,
                    "total": len(fallback.names),
                    "computed_at": fallback.computed_at,
                },
                "not_computed": exc.as_dict(),
                "hydra": trace.envelope(),
            },
        )

    names = sorted(closure.names)
    return ok(
        {
            "package": found[0]["name"],
            "depth": depth,
            "total": len(names),
            "returned": min(len(names), limit),
            "dependents": [
                {"name": n, "is_popular": n in digest().popular} for n in names[:limit]
            ],
        },
        trace,
        primary="q1_radius_nodes",
    )


@app.get("/api/maintainer-overlap/{pkg:path}")
async def maintainer_overlap(pkg: str, limit: int = Query(200, ge=1, le=2000)) -> dict[str, Any]:
    trace = Trace()
    client = state["client"]
    maintainers = await client.run_spec(
        "q4_maintainers_of", trace=trace, package_id=hash_key(pkg_key(pkg))
    )
    siblings: dict[str, list[str]] = {}
    for row in maintainers:
        owned = await client.run_spec(
            "q4_packages_of_maintainer", trace=trace, maintainer_id=hash_key(row["key"])
        )
        for item in owned:
            if item["name"] != pkg:
                siblings.setdefault(item["name"], []).append(row["username"])

    return ok(
        {
            "package": pkg,
            "maintainers": [row["username"] for row in maintainers],
            "total": len(siblings),
            "packages": [
                {"name": name, "shared_with": sorted(owners)}
                for name, owners in sorted(siblings.items())[:limit]
            ],
            "note": (
                "these share a publisher, not necessarily any code -- the worm "
                "spread through stolen credentials rather than dependencies"
            ),
        },
        trace,
    )


@app.get("/api/typosquats/{pkg:path}")
async def typosquats(pkg: str) -> dict[str, Any]:
    trace = Trace()
    rows = await state["client"].run_spec(
        "q5_typosquats", trace=trace, package_id=hash_key(pkg_key(pkg))
    )
    return ok(
        {
            "package": pkg,
            "total": len(rows),
            "neighbours": [
                {"name": r["name"], "distance": r["distance"], "is_popular": r["is_popular"]}
                for r in rows
            ],
            "note": "names within two edits, excluding packages under the same scope",
        },
        trace,
    )


@app.post("/api/lockfile")
async def check_lockfile(
    request: Request,
    installed_at: int | None = Query(None),
    depth: int = Query(DEFAULT_DEPTH),
) -> Any:
    if depth not in ALLOWED_DEPTHS:
        return JSONResponse(
            status_code=422,
            content={"status": "error", "message": f"depth must be one of {list(ALLOWED_DEPTHS)}"},
        )
    starting = starting_response()
    if starting is not None:
        return starting
    trace = Trace()
    try:
        parsed = lockfile_mod.parse_bytes(await request.body())
    except lockfile_mod.LockfileError as exc:
        return JSONResponse(
            status_code=422,
            content={"status": "error", "code": exc.code, "message": str(exc)},
        )

    d = digest()
    client = state["client"]

    exposed = None
    if d.incident_packages:
        exposed = await state["closures"].exposed_set(
            d.incident_packages, depth, trace=trace
        )

    # Only entries the advisory actually names are worth a per-version query;
    # everything else is answered from records already in memory.
    verdicts = []
    in_window: dict[tuple[str, str], bool] = {}
    remediations: dict[str, dict[str, Any]] = {}
    for entry in parsed.entries:
        hit = None
        if d.artifact(entry.name, entry.version) is not None:
            version_id = hash_key(ver_key(entry.name, entry.version))
            rows = await client.run_spec(
                "q3_advisory_hit", trace=trace, version_id=version_id
            )
            hit = rows[0] if rows else None

            if hit is not None and installed_at is not None:
                # Ask HydraDB whether the install fell inside the window, as an
                # integer predicate on the AFFECTS edge. The same comparison
                # could be done here in Python -- and is, to derive
                # before/inside/after -- but "was this live when you installed
                # it" is a question about an edge property, so the graph should
                # be the one answering it.
                confirmed = await client.run_spec(
                    "q3_advisory_hit_in_window",
                    trace=trace,
                    version_id=version_id,
                    installed_at=installed_at,
                )
                in_window[(entry.name, entry.version)] = bool(confirmed)

            # An EXPOSED verdict without a next step is half an answer. The
            # upgrade target is itself a graph answer -- the earliest clean
            # release after the window -- computed once per package. A refusal
            # degrades the remediation, never the verdict.
            if hit is not None and entry.name not in remediations:
                try:
                    clean = await client.run_spec(
                        "q12_clean_versions",
                        trace=trace,
                        package_id=hash_key(pkg_key(entry.name)),
                        after=hit.get("live_until") or 0,
                    )
                except NotComputed:
                    clean = None
                remediations[entry.name] = lockfile_mod.remediation(
                    entry.name, hit.get("live_until"), clean
                )
        verdicts.append(
            lockfile_mod.decide_entry(
                entry,
                advisory_hit=hit,
                package_in_graph=d.has_package(entry.name),
                version_in_graph=d.has_version(entry.name, entry.version),
                exposed_names=exposed.names if exposed else None,
                exposed_complete=exposed.complete if exposed else False,
                installed_at=installed_at,
            )
        )

    summary = lockfile_mod.rollup(verdicts)
    verdicts.sort(key=lambda v: (lockfile_mod.RANK[v.verdict], v.name))

    return ok(
        {
            "service": parsed.service,
            "lockfile_version": parsed.lockfile_version,
            "sha256": parsed.sha256,
            **summary,
            "coverage": {
                "entries": len(parsed.entries),
                "packages_in_graph": sum(1 for v in verdicts if v.signals["package_in_graph"]),
                "versions_in_graph": sum(1 for v in verdicts if v.signals["version_in_graph"]),
                "advisory_artifacts": len(d.artifacts),
                "note": (
                    "version-level records exist only for the packages the advisory "
                    "names, so entries without one are reported unknown rather than "
                    "clean -- absence of a record is not evidence of safety"
                ),
            },
            "entries": [
                {
                    "name": v.name,
                    "version": v.version,
                    "verdict": v.verdict,
                    "reason": v.reason,
                    "occurrences": v.occurrences,
                    "window": v.window,
                    # Whether HydraDB itself confirmed the install fell inside
                    # the window, via the predicate on the AFFECTS edge. None
                    # when no install time was supplied to compare against.
                    "in_window_per_graph": in_window.get((v.name, v.version)),
                    "advisory": v.advisory,
                    "remediation": remediations.get(v.name) if v.verdict == "EXPOSED" else None,
                    "signals": v.signals,
                }
                for v in verdicts
            ],
        },
        trace,
    )


@app.get("/api/forecast")
async def forecast() -> dict[str, Any]:
    """Where the stolen credentials can still publish, and the proof it matters.

    Computed once at boot from the maintainer pivot plus one radius per
    candidate; served from memory with every step replayed into the trace, ages
    attached, so the inspector shows exactly what produced it and when.
    """
    starting = starting_response()
    if starting is not None:
        return starting
    trace = Trace()
    d = digest()
    if not d.artifacts:
        return {
            "status": "unavailable",
            "result": None,
            "message": "this graph contains no advisory",
        }
    cached = state.get("forecast")
    if cached is None:
        return ok({"warming": True}, trace)
    for run in cached.steps:
        trace.replay(run)
    return ok(cached.payload, trace)


@app.get("/api/incident")
async def incident(live: bool = Query(False)) -> dict[str, Any]:
    """The incident this graph holds, and the multi-source traversal over it."""
    starting = starting_response()
    if starting is not None:
        return starting
    trace = Trace()
    d = digest()
    if not d.artifacts:
        return {
            "status": "unavailable",
            "result": None,
            "message": "this graph contains no advisory",
        }

    payload: dict[str, Any] = {
        "advisory": d.advisory_key,
        "is_the_demonstrated_incident": d.has_incident,
        "packages": list(d.incident_packages),
        "artifacts": len(d.artifacts),
        "window": d.window,
        "publishes": sorted(
            (
                {"name": a.name, "version": a.version, "published_at": a.published_at}
                for a in d.artifacts.values()
            ),
            key=lambda row: row["published_at"],
        ),
    }

    exposed = state["closures"]._exposed.get(DEFAULT_DEPTH)
    if exposed is not None:
        payload["exposed"] = {
            "depth": exposed.depth,
            "total": len(exposed.names),
            "complete": exposed.complete,
            "computed_at": exposed.computed_at,
        }
    else:
        payload["exposed"] = {"warming": state["closures"].warming}

    # How the attack actually unfolded. The waves need no traversals; the
    # exposure curve folds over per-seed closures the boot warm already
    # computed, so it costs nothing beyond what has been queried.
    artifacts = list(d.artifacts.values())
    waves = timeline.group_waves(artifacts)
    payload["waves"] = [
        {
            "wave": w.index,
            "from": w.started,
            "from_iso": iso(w.started),
            "to": w.ended,
            "seconds": w.seconds,
            "versions": w.versions,
            "packages": w.packages,
        }
        for w in waves
    ]

    cached_closures = {
        seed: closure.names
        for (seed, depth), closure in state["closures"]._radius.items()
        if depth == DEFAULT_DEPTH
    }
    if cached_closures:
        curve = timeline.exposure_curve(artifacts, cached_closures)
        payload["exposure_curve"] = curve
        payload["saturation"] = timeline.saturation(curve)
    else:
        payload["exposure_curve"] = None
        payload["saturation"] = {"warming": state["closures"].warming}

    if live:
        # One server-side call resolving every compromised package at once.
        rows = await state["client"].run_spec(
            "q2_incident_paths",
            trace=trace,
            interpolate={"seed_values": seed_values(list(d.incident_packages), known=set(d.names))},
            max_len=DEFAULT_DEPTH,
            path_count=200,
            result_limit=2000,
        )
        payload["paths"] = {
            "returned": len(rows),
            "sampled": True,
            "note": (
                "pathCount is a shortest-paths budget rather than a depth, so this "
                "is a bounded sample; the counts above come from the traversal"
            ),
        }

    return ok(payload, trace)


# Static mounts come last: they claim "/", so anything registered after them
# would be shadowed. The guard keeps a fresh clone working before anyone has
# run a UI build.
DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
SAMPLES = Path(__file__).resolve().parent.parent / "data" / "fixtures" / "lockfiles"

if SAMPLES.is_dir():
    app.mount("/samples", StaticFiles(directory=str(SAMPLES)), name="samples")
if DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(DIST), html=True), name="web")
