// Which (name, version) pairs the graph actually holds, so the lockfile path
// can tell "no record" apart from "no advisory".
MATCH (v:Version)
RETURN v.name AS name, v.version AS version, v.compromised AS compromised
