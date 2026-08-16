SELECT Name AS name, COUNT(*) AS version_count,
       COUNTIF(VersionInfo.IsRelease) AS release_count,
       UNIX_SECONDS(MAX(UpstreamPublishedAt)) AS last_published
FROM `bigquery-public-data.deps_dev_v1.PackageVersions`
WHERE SnapshotAt >= TIMESTAMP("2026-08-10") AND SnapshotAt < TIMESTAMP("2026-08-11")
  AND System = 'NPM'
GROUP BY Name
