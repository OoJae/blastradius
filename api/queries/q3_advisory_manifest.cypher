// Every artifact this advisory names, with the window it was live in.
//
// Anchored on the advisory id: a bounded single-hop read, not a scan. This is
// the pre-filter that decides which lockfile entries are worth a per-version
// query, and where the replay's publish times come from. Read from the graph
// rather than from data/incident/ so the served numbers are the graph's.
MATCH (a:Advisory {id: $advisory_id})-[r:AFFECTS]->(v:Version)
RETURN v.name AS name, v.version AS version, v.published_at AS published_at,
       r.live_from AS live_from, r.live_until AS live_until
