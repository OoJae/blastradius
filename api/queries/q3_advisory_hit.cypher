// Was this exact artifact named by an advisory, and when was it live?
// Single-hop, so a destination anchor is fine: only variable-length patterns
// require a fixed source.
MATCH (a:Advisory)-[r:AFFECTS]->(v:Version {id: $version_id})
RETURN a.key AS advisory, a.cve AS cve, a.severity AS severity,
       r.live_from AS live_from, r.live_until AS live_until
