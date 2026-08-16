// Blast radius: every package that transitively depends on the seed.
//
// Anchored on a fixed source id and walked over the materialised reverse
// projection, because HydraDB rejects a destination-anchored variable-length
// MATCH ("variable-length MATCH requires a fixed source id") and reversing the
// arrow is rejected identically. See docs/VERIFICATION.md.
//
// The distinct count is taken client-side from this projection: count(DISTINCT
// d.id) is not executable in this Cypher subset.
MATCH (c:Package {id: $seed_id})-[:PKG_DEPENDED_BY*1..<<depth>>]->(d:Package)
RETURN DISTINCT d.key AS key, d.name AS name, d.is_popular AS is_popular
