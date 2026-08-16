#!/usr/bin/env bash
# HTTP round-trip against a running HydraDB node.
#
# Deliberately uses the same statement shapes the loader will use, so this
# doubles as a contract test for HydraDB's strict batched-upsert rules:
#   - MERGE matches on id only (a label in the MERGE pattern is rejected)
#   - exactly one SET label
#   - every SET value must read a field from the row map (no literals)
set -euo pipefail

HYDRA_HTTP_URL="${HYDRA_HTTP_URL:-http://127.0.0.1:8443}"
HYDRA_TOKEN="${HYDRA_TOKEN:-local-development-token-32-bytes}"
HYDRA_NAMESPACE="${HYDRA_NAMESPACE:-default}"
HYDRA_GRAPH_ID="${HYDRA_GRAPH_ID:-default}"
HYDRA_CELL_ID="${HYDRA_CELL_ID:-cell-0}"

URL="${HYDRA_HTTP_URL}/v1/graphs/${HYDRA_GRAPH_ID}/query"
NODE_ID=900000001
NODE_KEY="smoke:http"

query() {
    curl -fsS "$URL" \
        -H "Authorization: Bearer ${HYDRA_TOKEN}" \
        -H "X-Graph-Namespace: ${HYDRA_NAMESPACE}" \
        -H 'Content-Type: application/json' \
        --data @-
}

echo "== HTTP write (batched UNWIND upsert)"
write_response=$(
    jq -nc --argjson id "$NODE_ID" --arg key "$NODE_KEY" --arg cell "$HYDRA_CELL_ID" '{
        cell_id: $cell,
        query: "UNWIND $rows AS row MERGE (n {id: row.vertex}) SET n:Smoke, n.key = row.key",
        parameters: {rows: [{vertex: $id, key: $key}]}
    }' | query
)
echo "   $write_response"

# A mutation returns an empty envelope. That is success, not failure - the
# loader must never treat empty columns/rows as a failed write.
if [ "$(jq -r '.columns | length' <<<"$write_response")" != "0" ] ||
    [ "$(jq -r '.rows | length' <<<"$write_response")" != "0" ]; then
    echo "FAIL: expected an empty mutation envelope" >&2
    exit 1
fi
echo "   OK: empty mutation envelope (columns=[], rows=[])"

echo "== HTTP read-back"
read_response=$(
    jq -nc --argjson id "$NODE_ID" --arg cell "$HYDRA_CELL_ID" '{
        cell_id: $cell,
        query: "MATCH (n:Smoke {id: $id}) RETURN n.key AS key",
        parameters: {id: $id}
    }' | query
)
echo "   $read_response"

got=$(jq -r '.rows[0][0].value // empty' <<<"$read_response")
if [ "$got" != "$NODE_KEY" ]; then
    echo "FAIL: expected key '${NODE_KEY}', got '${got}'" >&2
    exit 1
fi
echo "   OK: read back key='${got}'"

echo "== HTTP cleanup"
jq -nc --argjson id "$NODE_ID" --arg cell "$HYDRA_CELL_ID" '{
    cell_id: $cell,
    query: "UNWIND $vertices AS row MATCH (n {id: row.vertex}) DETACH DELETE n",
    parameters: {vertices: [{vertex: $id}]}
}' | query >/dev/null
echo "   OK: smoke node deleted"

echo "HTTP smoke passed"
