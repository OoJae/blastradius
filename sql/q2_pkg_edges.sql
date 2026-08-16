WITH m AS (
  SELECT Name AS pkg, Version AS version, d.dep_name AS dep, d.kind AS kind
  FROM `bigquery-public-data.deps_dev_v1.NPMRequirements`,
  UNNEST(ARRAY_CONCAT(
    ARRAY(SELECT AS STRUCT x.Name AS dep_name, 1 AS kind FROM UNNEST(Dependencies) x),
    ARRAY(SELECT AS STRUCT x.Name, 2 FROM UNNEST(DevDependencies) x),
    ARRAY(SELECT AS STRUCT x.Name, 4 FROM UNNEST(PeerDependencies) x),
    ARRAY(SELECT AS STRUCT x.Name, 8 FROM UNNEST(OptionalDependencies) x)
  )) AS d
  WHERE SnapshotAt >= TIMESTAMP("2026-08-10") AND SnapshotAt < TIMESTAMP("2026-08-11")
)
SELECT pkg, dep, BIT_OR(kind) AS kinds, COUNT(DISTINCT version) AS via_versions,
       COUNT(DISTINCT IF(kind = 1, version, NULL)) AS runtime_versions
FROM m
WHERE dep IS NOT NULL AND dep != '' AND pkg != dep
GROUP BY pkg, dep
