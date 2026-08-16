// Does the graph hold this exact artifact? Only the packages the advisory
// names have Version nodes, which is why a lockfile entry can be legitimately
// unprovable rather than clean.
MATCH (v:Version {id: $version_id})
RETURN v.key AS key, v.name AS name, v.version AS version,
       v.published_at AS published_at, v.compromised AS compromised
