// The autocomplete index is built from this one query at startup.
//
// A live prefix scan costs 4,940 ms on this slice and there is no index DDL in
// this Cypher subset, so lookup is served from an in-process sorted array built
// here. The names are HydraDB's; the lookup is a binary search over them and
// cannot return a name HydraDB did not give us.
MATCH (p:Package)
RETURN p.key AS key, p.name AS name, p.is_popular AS is_popular
