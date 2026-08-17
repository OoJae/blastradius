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
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

from api import lockfile as lockfile_mod
from api.closure import ClosureCache
from api.digest import INCIDENT_ADVISORY, GraphDigest
from api.hydra import HydraClient, NotComputed
from api.queries import ALLOWED_DEPTHS, seed_values
from api.trace import Trace
from ingest.ids import adv_key, hash_key, pkg_key, ver_key

DEFAULT_DEPTH = 3
state: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    client = HydraClient.open()
    state["client"] = client
    print("building the graph digest...", flush=True)
    started = time.perf_counter()
    digest = await GraphDigest.build(client)
    state["digest"] = digest
    state["closures"] = ClosureCache(client)
    print(
        f"digest ready in {time.perf_counter() - started:.1f}s: "
        f"{len(digest.names):,} packages, advisory {digest.advisory_key or 'none'}",
        flush=True,
    )
    if digest.incident_packages:
        state["warm"] = asyncio.create_task(
            state["closures"].warm(digest.incident_packages, DEFAULT_DEPTH)
        )
    try:
        yield
    finally:
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


@app.get("/api/health")
async def health() -> dict[str, Any]:
    ready = "digest" in state
    return {"status": "ok" if ready else "starting", "ready": ready}


@app.get("/api/stats")
async def stats() -> dict[str, Any]:
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
            "built_at": d.built_at,
            "boot_queries": [step.as_step() for step in d.boot],
        },
    }


@app.get("/api/suggest")
async def suggest(
    q: str = Query(""),
    limit: int = Query(10, ge=1, le=50),
    live: bool = Query(False),
) -> dict[str, Any]:
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
    for entry in parsed.entries:
        hit = None
        if d.artifact(entry.name, entry.version) is not None:
            rows = await client.run_spec(
                "q3_advisory_hit",
                trace=trace,
                version_id=hash_key(ver_key(entry.name, entry.version)),
            )
            hit = rows[0] if rows else None
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
                    "advisory": v.advisory,
                    "signals": v.signals,
                }
                for v in verdicts
            ],
        },
        trace,
    )


@app.get("/api/incident")
async def incident(live: bool = Query(False)) -> dict[str, Any]:
    """The incident this graph holds, and the multi-source traversal over it."""
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
