// The same hit, with the live-window comparison evaluated by HydraDB as an
// integer predicate on the edge. This is the temporal question the product is
// built around.
//
// It returns nothing when the install fell outside the window, which is
// indistinguishable from "no advisory" -- so the API runs it alongside
// q3_advisory_hit and reports "resolved a malicious artifact" and "installed
// while it was live" as two separate facts.
MATCH (a:Advisory)-[r:AFFECTS]->(v:Version {id: $version_id})
WHERE r.live_from <= $installed_at AND r.live_until >= $installed_at
RETURN a.key AS advisory, r.live_from AS live_from, r.live_until AS live_until
