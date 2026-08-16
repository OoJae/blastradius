// Resolve a package through the graph, so a 404 is an answer HydraDB gave and
// appears in the query trace like any other.
MATCH (p:Package {id: $package_id})
RETURN p.key AS key, p.name AS name, p.is_popular AS is_popular
