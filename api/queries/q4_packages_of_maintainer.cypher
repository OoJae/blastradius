// What else that publisher owns.
//
// The worm travelled through stolen credentials rather than dependencies, so
// this is a different question from the blast radius and must not be conflated
// with it: a package here shares an owner, not necessarily any code.
MATCH (m:Maintainer {id: $maintainer_id})-[:MAINTAINS]->(q:Package)
RETURN q.key AS key, q.name AS name, q.is_popular AS is_popular
