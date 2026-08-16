// The graph-native form of autocomplete.
//
// Measured at 4,940 ms on the demo slice, so /api/suggest does not use it in
// the hot path -- but /api/suggest?live=1 runs THIS statement and returns the
// same shape, so the startup index can be checked against the graph rather than
// trusted. parse-check executes it on every run.
MATCH (p:Package)
WHERE p.name STARTS WITH $prefix
RETURN p.key AS key, p.name AS name
LIMIT 10
