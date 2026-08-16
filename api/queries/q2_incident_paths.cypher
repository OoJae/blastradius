// The whole incident in one server-side call: every compromised package is a
// seed and HydraDB resolves all 42 itself, replacing 42 client round trips.
//
// sourceLabel/sourceProperty/sourceValues/relTypes must be inline literals; a
// driver parameter is rejected ("composite parameter is only supported as an
// UNWIND input"). {seed_values} is produced only by api.queries.seed_values(),
// from names the digest confirmed exist in the graph, escaped through
// ingest.hydra.cypher_string_list.
//
// Seeded with the 42 compromised PACKAGE keys, not the 84 version keys: a
// Version seed over a Package-to-Package edge returns zero paths.
CALL algo.MSpaths({
  sourceLabel: 'Package',
  sourceProperty: 'key',
  sourceValues: <<seed_values>>,
  relTypes: ['PKG_DEPENDS_ON'],
  relDirection: 'incoming',
  maxLen: $max_len,
  pathCount: $path_count,
  resultLimit: $result_limit
}) YIELD path
RETURN path
