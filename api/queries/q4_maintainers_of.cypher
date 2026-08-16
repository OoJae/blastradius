// Who publishes this package. Single hop: [:MAINTAINS*1..1] is rejected, since
// the restriction is about variable-length syntax rather than hop count.
MATCH (m:Maintainer)-[:MAINTAINS]->(p:Package {id: $package_id})
RETURN m.key AS key, m.username AS username
