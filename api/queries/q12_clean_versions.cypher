// The remediation target: releases of this package that are not compromised
// and were published after the advisory's live window closed. The earliest of
// them is picked client-side, because this Cypher subset has no min().
//
// Single hop with a destination anchor, which is fine: only variable-length
// patterns require a fixed source. The compromised flag is a bool --
// the loader stores it as a real bool, so the comparison is against `false`.
MATCH (v:Version)-[:VERSION_OF]->(p:Package {id: $package_id})
WHERE v.compromised = false AND v.published_at > $after
RETURN v.version AS version, v.published_at AS published_at
