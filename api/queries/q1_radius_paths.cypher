// A bounded sample of dependency chains into the seed, for the graph view.
//
// pathCount is a shortest-paths *budget*, not a depth: too small a value
// silently returns a shallow slice, so the counts shown to a user always come
// from q1_radius_nodes and never from here.
CALL algo.SSpaths({
  sourceNode: $seed_id,
  relTypes: ['PKG_DEPENDS_ON'],
  relDirection: 'incoming',
  maxLen: $max_len,
  pathCount: $path_count,
  resultLimit: $result_limit
}) YIELD path
RETURN path
