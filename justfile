# BlastRadius task runner. `just` with no arguments lists the targets.

export HYDRA_UID := `id -u`
export HYDRA_GID := `id -g`

token := "local-development-token-32-bytes"
ready_url := "http://127.0.0.1:9090/readyz"

default:
    @just --list

# Start HydraDB and block until it reports ready.
up:
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p hydradb-data/store hydradb-data/cache hydradb-data/minio
    if [ ! -s hydradb-data/auth-token ]; then
        printf '%s\n' '{{token}}' > hydradb-data/auth-token
        echo "wrote hydradb-data/auth-token"
    fi
    docker compose up -d
    # MinIO needs the bucket to exist before HydraDB writes to it.
    docker compose exec -T minio mc alias set local http://127.0.0.1:9000 blastradius blastradius-dev-secret >/dev/null 2>&1 || true
    docker compose exec -T minio mc mb --ignore-existing local/blastradius >/dev/null 2>&1 || true
    # The host-side poll is the authoritative readiness gate: the image may not
    # ship curl/wget, so a container healthcheck cannot be relied on.
    echo -n "waiting for {{ready_url}} "
    for i in $(seq 1 60); do
        if curl -fsS "{{ready_url}}" >/dev/null 2>&1; then
            echo "-> ready after ${i}s"
            exit 0
        fi
        echo -n "."
        sleep 1
    done
    echo " TIMEOUT"
    docker compose logs --tail 40 hydradb
    exit 1

down:
    docker compose down

# Stop and delete all graph state. Use between the throughput spike and fixtures.
nuke: down
    rm -rf hydradb-data/store hydradb-data/cache hydradb-data/minio
    @echo "graph state wiped"

logs:
    docker compose logs -f hydradb

# HTTP + Bolt round-trips against a running node.
smoke:
    bash scripts/http_smoke.sh
    uv run python scripts/bolt_smoke.py

# Path-procedure semantics check on a ~10 node toy graph.
toy:
    uv run python scripts/toy_mspaths.py

# Write-throughput and traversal-latency measurement (100k nodes / 300k edges).
spike:
    uv run python scripts/spike_load.py

# Load the checked-in fixture graph.
ingest-demo:
    uv run python -m ingest.load load-fixture

# Unit tests; no database required.
test:
    uv run pytest

# Tests that need a running node with fixtures loaded.
test-live:
    uv run pytest -m live

ingest:
    @echo "not implemented until Phase 1"

# Evaluate against the real incident and render docs/EVAL_REPORT.md.
eval:
    uv run python -m eval.run --dir "$(cat data/slice/CURRENT 2>/dev/null || echo data/slice/demo-20260810)"

# Run the API against whatever graph is currently loaded.
web-install:
    cd web && npm ci

# Build the UI into web/dist, which `just dev` then serves on the same origin.
web-build:
    cd web && npm run build

# UI dev server on :5173, proxying /api to the API on :8000.
web-dev:
    cd web && npm run dev

dev:
    uv run uvicorn api.main:app --reload --port 8000

# HydraDB has no network-reachable EXPLAIN, so this executes each query.
parse-check:
    uv run python scripts/parse_check.py
