// Names within Damerau-Levenshtein distance 2, materialised at ingest because
// this Cypher subset has no functions.
//
// LIMIT is a literal rather than a parameter: parameterised SKIP/LIMIT is not
// among the shapes verified against this server, and the cap is never binding
// at the sizes involved.
MATCH (p:Package {id: $package_id})-[s:SIMILAR_NAME]->(q:Package)
RETURN q.key AS key, q.name AS name, q.is_popular AS is_popular,
       s.distance AS distance
ORDER BY distance
LIMIT 50
