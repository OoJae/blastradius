// Why each statement is shaped the way it is. Lifted from the comments in
// api/queries/*.cypher, so the constraint-driven design story is available at
// the moment a reader is looking at the query itself.

export const QUERY_NOTES: Record<string, string> = {
  q1_radius_nodes:
    'A blast radius asks "who depends on me", which is naturally destination-anchored — but HydraDB rejects a variable-length MATCH that is not anchored on a fixed source id, and reversing the arrow is rejected identically. So the loader materialises a reverse projection, PKG_DEPENDED_BY, and the radius becomes an outgoing traversal from a fixed seed. The depth bound is interpolated from a whitelist because it is part of the pattern grammar and cannot be a parameter.',
  q1_radius_paths:
    'pathCount is a shortest-paths budget rather than a depth, so this returns a bounded sample of chains and can be shallow. Every count shown in the interface comes from the traversal above, never from this.',
  q2_incident_paths:
    'One server-side call resolves all 42 compromised packages at once, replacing 42 client round trips. The seed list must be an inline literal: a driver parameter is rejected with "composite parameter is only supported as an UNWIND input". It seeds with package keys, not version keys — a Version seed over a Package-to-Package edge returns nothing.',
  q3_advisory_hit:
    'Single-hop, so anchoring on the destination is fine; only variable-length patterns require a fixed source.',
  q3_advisory_hit_in_window:
    'The temporal question — "was this artifact live when you installed it" — evaluated by HydraDB as an integer predicate on the AFFECTS edge rather than in application code. It returns nothing when the install fell outside the window, which is indistinguishable from "no advisory", so it runs alongside the plain hit and the two facts are reported separately.',
  q3_advisory_manifest:
    'Anchored on the advisory id: a bounded single-hop read rather than a scan. This is the pre-filter that decides which lockfile entries are worth a per-version query.',
  q4_maintainers_of:
    'Single hop. [:MAINTAINS*1..1] is rejected — the restriction is about variable-length syntax, not hop count.',
  q4_packages_of_maintainer:
    'The worm travelled through stolen credentials rather than dependencies, so this is a different question from the blast radius. A package here shares an owner, not necessarily any code.',
  q5_typosquats:
    'Damerau-Levenshtein neighbours, materialised at ingest because this Cypher subset has no functions. LIMIT is a literal rather than a parameter.',
  q6_package_lookup:
    'Every endpoint resolves its package through the graph first, so a 404 is an answer HydraDB gave and appears in this trace like any other.',
  q7_version_lookup:
    'Only the packages the advisory names have Version nodes, which is why a lockfile entry can be legitimately unprovable rather than clean.',
  q8_label_count:
    'Boot-time census, run once at startup and never inside a request — the Package count alone measures over two seconds. Edge counts are absent on purpose: an unanchored count over the dependency edges is refused by admission control.',
  q9_package_roster:
    'The autocomplete index is built from this one query at startup. The names are HydraDB’s; the lookup is a binary search over them and cannot return a name HydraDB did not give us.',
  q10_version_roster:
    'Which (name, version) pairs the graph actually holds, so the lockfile path can tell "no record" apart from "no advisory".',
  q11_prefix_search:
    'The graph-native form of autocomplete, about five seconds on this data because there is no index DDL in this Cypher subset. The hot path does not use it, but ?live=1 runs exactly this so the cache can be checked against the graph rather than trusted.',
}
