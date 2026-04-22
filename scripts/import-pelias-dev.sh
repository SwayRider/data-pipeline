#!/bin/bash
#
# import-pelias-dev.sh
#
# Import Pelias data into the dev server.
# Supports two independent operations:
#   1. Restore ES snapshot (run on ES machine)
#   2. Deploy configs + WOF + placeholder (run on Pelias services machine)
#
# Usage:
#   ./import-pelias-dev.sh --es-snapshot <file> --data <file> [options]
#   ./import-pelias-dev.sh --es-snapshot <file>                     # ES only
#   ./import-pelias-dev.sh --data <file>                            # data only
#
# Options:
#   --es-snapshot FILE     Path to pelias-es-snapshot.tar.bz2
#   --data FILE            Path to pelias-data.tar.bz2
#   --volumes-path PATH    Service volumes (default: $VOLUMES_DATA_PATH)
#   --geodata-path PATH    Geodata directory (default: $GEODATA_PATH)
#   --es-host HOST         Elasticsearch host (default: localhost)
#   --es-port PORT         Elasticsearch port (default: 39200)
#   --repo-name NAME       Snapshot repository name (default: pelias_repo)
#   --regions REGIONS      Comma-separated regions (default: auto-detect)
#   --dry-run              Show what would be done without executing
#
# After running, restart Pelias services:
#   docker compose -f infra/dev/layer-10/compose.yaml restart
#

set -euo pipefail

# --- Defaults ---
VOLUMES_DATA_PATH="${VOLUMES_DATA_PATH:-/mnt/hdd-pool/swayrider/dev/volumes}"
GEODATA_PATH="${GEODATA_PATH:-/mnt/hdd-pool/swayrider/geodata}"
ES_HOST="localhost"
ES_PORT="39200"
REPO_NAME="pelias_repo"
REGIONS=""
DRY_RUN=false
ES_SNAPSHOT_FILE=""
DATA_FILE=""

# --- Parse arguments ---
while [[ $# -gt 0 ]]; do
    case "$1" in
        --es-snapshot)
            ES_SNAPSHOT_FILE="$2"; shift 2 ;;
        --data)
            DATA_FILE="$2"; shift 2 ;;
        --volumes-path)
            VOLUMES_DATA_PATH="$2"; shift 2 ;;
        --geodata-path)
            GEODATA_PATH="$2"; shift 2 ;;
        --es-host)
            ES_HOST="$2"; shift 2 ;;
        --es-port)
            ES_PORT="$2"; shift 2 ;;
        --repo-name)
            REPO_NAME="$2"; shift 2 ;;
        --regions)
            REGIONS="$2"; shift 2 ;;
        --dry-run)
            DRY_RUN=true; shift ;;
        -h|--help)
            sed -n '2,/^$/s/^# \?//p' "$0"
            exit 0 ;;
        -*)
            echo "Unknown option: $1" >&2; exit 1 ;;
        *)
            echo "Unexpected argument: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "$ES_SNAPSHOT_FILE" && -z "$DATA_FILE" ]]; then
    echo "Error: at least one of --es-snapshot or --data is required" >&2
    echo "Run with --help for usage" >&2
    exit 1
fi

if [[ -n "$ES_SNAPSHOT_FILE" && ! -f "$ES_SNAPSHOT_FILE" ]]; then
    echo "Error: ES snapshot archive not found: $ES_SNAPSHOT_FILE" >&2
    exit 1
fi

if [[ -n "$DATA_FILE" && ! -f "$DATA_FILE" ]]; then
    echo "Error: data archive not found: $DATA_FILE" >&2
    exit 1
fi

ES_SNAPSHOT_DIR="${VOLUMES_DATA_PATH}/elasticsearch/snapshots"
ES_URL="http://${ES_HOST}:${ES_PORT}"

# --- Helper functions ---
es_request() {
    local method="$1"
    local path="$2"
    local data="${3:-}"
    local args=(-s -w "\n%{http_code}" -X "$method" -H "Content-Type: application/json")
    if [[ -n "$data" ]]; then
        args+=(-d "$data")
    fi
    local response
    response=$(curl "${args[@]}" "${ES_URL}${path}" 2>/dev/null)
    local http_code
    http_code=$(echo "$response" | tail -1)
    local body
    body=$(echo "$response" | sed '$d')
    if [[ "$http_code" -ge 200 && "$http_code" -lt 300 ]]; then
        echo "$body"
        return 0
    else
        echo "ES request failed: $method $path (HTTP $http_code)" >&2
        echo "$body" >&2
        return 1
    fi
}

wait_for_es() {
    echo "Waiting for Elasticsearch at ${ES_URL}..."
    local timeout=60
    local start=$SECONDS
    while (( SECONDS - start < timeout )); do
        if response=$(es_request GET "/_cluster/health" 2>/dev/null); then
            local status
            status=$(echo "$response" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")
            if [[ "$status" == "green" || "$status" == "yellow" ]]; then
                echo "Elasticsearch ready (status: $status)"
                return 0
            fi
        fi
        sleep 3
    done
    echo "Error: Elasticsearch not ready after ${timeout}s" >&2
    return 1
}

# =====================
# ES Snapshot Restore
# =====================
restore_es_snapshot() {
    local archive="$1"
    echo ""
    echo "========================================="
    echo "  ES Snapshot Restore"
    echo "========================================="
    echo "Archive:      $archive"
    echo "ES:           $ES_URL"
    echo "Snapshot dir: $ES_SNAPSHOT_DIR"
    echo ""

    wait_for_es

    echo "--- Extracting ES snapshot archive ---"
    WORK_DIR=$(mktemp -d)
    trap "rm -rf $WORK_DIR" EXIT

    tar -xjf "$archive" -C "$WORK_DIR"
    echo "Extracted to $WORK_DIR"

    echo "--- Setting up snapshot repository ---"
    mkdir -p "$ES_SNAPSHOT_DIR"
    cp -r "$WORK_DIR"/* "$ES_SNAPSHOT_DIR/"

    echo "Fixing snapshot directory permissions..."
    docker exec sw-dev-elasticsearch \
        chown -R 1000:1000 /usr/share/elasticsearch/snapshots 2>/dev/null || true

    echo "Registering snapshot repository '${REPO_NAME}'..."
    REPO_PAYLOAD="{\"type\":\"fs\",\"settings\":{\"location\":\"/usr/share/elasticsearch/snapshots\",\"compress\":true}}"
    if ! es_request PUT "/_snapshot/${REPO_NAME}" "$REPO_PAYLOAD" > /dev/null; then
        echo "Error: failed to register snapshot repository" >&2
        exit 1
    fi
    echo "Repository registered"

    echo "--- Available snapshots ---"
    SNAPSHOTS=$(es_request GET "/_snapshot/${REPO_NAME}/_all" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for s in data.get('snapshots', []):
    print(s['snapshot'])
" 2>/dev/null || echo "")

    if [[ -z "$SNAPSHOTS" ]]; then
        echo "Error: no snapshots found in repository" >&2
        exit 1
    fi
    echo "$SNAPSHOTS"

    # Auto-detect regions from snapshot names if not specified
    if [[ -z "$REGIONS" ]]; then
        REGIONS=$(echo "$SNAPSHOTS" | sed 's/^pelias_//;s/-[^-]*$//' | sort -u | tr '\n' ',' | sed 's/,$//')
        echo "Auto-detected regions: $REGIONS"
    fi

    IFS=',' read -ra REGION_LIST <<< "$REGIONS"
    for region in "${REGION_LIST[@]}"; do
        echo ""
        echo "--- Restoring region: $region ---"

        SNAPSHOT_NAME=""
        while IFS= read -r snap; do
            if [[ "$snap" == pelias_${region}-* ]]; then
                SNAPSHOT_NAME="$snap"
                break
            fi
        done <<< "$SNAPSHOTS"

        if [[ -z "$SNAPSHOT_NAME" ]]; then
            echo "  Warning: no snapshot found for region '$region', skipping" >&2
            continue
        fi

        if $DRY_RUN; then
            echo "  Would restore snapshot: $SNAPSHOT_NAME"
            continue
        fi

        echo "  Restoring snapshot: $SNAPSHOT_NAME"
        RESTORE_PAYLOAD="{\"indices\":\"*\",\"ignore_unavailable\":true,\"include_global_state\":false}"
        if ! es_request POST "/_snapshot/${REPO_NAME}/${SNAPSHOT_NAME}/_restore?wait_for_completion=true" "$RESTORE_PAYLOAD" > /dev/null; then
            echo "  Warning: restore may have had issues, continuing..." >&2
        fi

        INDEX_NAME="$SNAPSHOT_NAME"
        ALIAS_NAME="pelias_${region}"

        echo "  Updating alias: $ALIAS_NAME -> $INDEX_NAME"
        ALIAS_PAYLOAD="{\"actions\":[{\"remove\":{\"index\":\"*\",\"alias\":\"${ALIAS_NAME}\",\"ignore_unavailable\":true}},{\"add\":{\"index\":\"${INDEX_NAME}\",\"alias\":\"${ALIAS_NAME}\"}}]}"
        if ! es_request POST "/_aliases" "$ALIAS_PAYLOAD" > /dev/null; then
            ALIAS_PAYLOAD="{\"actions\":[{\"add\":{\"index\":\"${INDEX_NAME}\",\"alias\":\"${ALIAS_NAME}\"}}]}"
            es_request POST "/_aliases" "$ALIAS_PAYLOAD" > /dev/null || true
        fi
        echo "  Alias updated"
    done

    echo ""
    echo "ES snapshot restore complete"
}

# =====================
# Data Deployment
# =====================
deploy_data() {
    local archive="$1"
    echo ""
    echo "========================================="
    echo "  Data Deployment (configs + WOF + placeholder)"
    echo "========================================="
    echo "Archive:       $archive"
    echo "Volumes path:  $VOLUMES_DATA_PATH"
    echo "Geodata path:  $GEODATA_PATH"
    echo ""

    echo "--- Extracting data archive ---"
    DATA_WORK_DIR=$(mktemp -d)
    trap "rm -rf $DATA_WORK_DIR" EXIT

    tar -xjf "$archive" -C "$DATA_WORK_DIR"
    echo "Extracted to $DATA_WORK_DIR"

    # Detect regions from the archive
    if [[ -z "$REGIONS" ]]; then
        REGIONS=$(find "$DATA_WORK_DIR" -name "pelias-prod-*.json" \
            -exec basename {} \; | sed 's/pelias-prod-//;s/.json//' \
            | tr '\n' ',' | sed 's/,$//')
        echo "Auto-detected regions: $REGIONS"
    fi

    if [[ -z "$REGIONS" ]]; then
        echo "Error: no regions found in data archive and none specified" >&2
        exit 1
    fi

    IFS=',' read -ra REGION_LIST <<< "$REGIONS"
    for region in "${REGION_LIST[@]}"; do
        echo ""
        echo "--- Deploying region: $region ---"

        if $DRY_RUN; then
            echo "  Would deploy configs and WOF for $region"
            continue
        fi

        # Deploy prod config to API config path
        API_CONFIG_DIR="${VOLUMES_DATA_PATH}/pelias/${region}/api/config"
        PROD_CONFIG_SRC="$DATA_WORK_DIR/pelias-prod-${region}.json"
        if [[ -f "$PROD_CONFIG_SRC" ]]; then
            mkdir -p "$API_CONFIG_DIR"
            cp "$PROD_CONFIG_SRC" "$API_CONFIG_DIR/pelias.json"
            echo "  Deployed API config to $API_CONFIG_DIR/pelias.json"
        else
            echo "  Warning: prod config not found: $PROD_CONFIG_SRC" >&2
        fi

        # Deploy prod config to PIP config path
        PIP_CONFIG_DIR="${VOLUMES_DATA_PATH}/pelias/${region}/pip/config"
        if [[ -f "$PROD_CONFIG_SRC" ]]; then
            mkdir -p "$PIP_CONFIG_DIR"
            cp "$PROD_CONFIG_SRC" "$PIP_CONFIG_DIR/pelias.json"
            echo "  Deployed PIP config to $PIP_CONFIG_DIR/pelias.json"
        fi

        # Extract WOF data for this region
        PIP_WOF_DIR="${VOLUMES_DATA_PATH}/pelias/${region}/pip/whosonfirst"
        WOF_ARCHIVE="$DATA_WORK_DIR/${region}/wof.tar.gz"
        if [[ -f "$WOF_ARCHIVE" ]]; then
            mkdir -p "$PIP_WOF_DIR"
            echo "  Extracting WOF data from $WOF_ARCHIVE"
            tar -xzf "$WOF_ARCHIVE" -C "$PIP_WOF_DIR"
        else
            echo "  Warning: WOF archive not found: $WOF_ARCHIVE" >&2
        fi
    done

    # Deploy placeholder data
    echo ""
    echo "--- Deploying placeholder data ---"
    PLACEHOLDER_DEST="${VOLUMES_DATA_PATH}/pelias/placeholder/data"
    PLACEHOLDER_SRC="$DATA_WORK_DIR/store.sqlite3.gz"

    if [[ -f "$PLACEHOLDER_SRC" ]]; then
        mkdir -p "$PLACEHOLDER_DEST"
        cp "$PLACEHOLDER_SRC" "$PLACEHOLDER_DEST/"
        echo "  Deployed placeholder data to $PLACEHOLDER_DEST/"
    else
        echo "  Warning: placeholder data not found in archive" >&2
    fi

    echo ""
    echo "Data deployment complete"
}

# =====================
# Main
# =====================
echo "=== Pelias Import to Dev Server ==="

if [[ -n "$ES_SNAPSHOT_FILE" ]]; then
    restore_es_snapshot "$ES_SNAPSHOT_FILE"
fi

if [[ -n "$DATA_FILE" ]]; then
    deploy_data "$DATA_FILE"
fi

echo ""
echo "========================================="
echo "  Import Complete"
echo "========================================="
echo ""
if [[ -n "$ES_SNAPSHOT_FILE" ]]; then
    echo "ES:   Restored ($ES_URL)"
fi
if [[ -n "$DATA_FILE" ]]; then
    echo "Data: Deployed to $VOLUMES_DATA_PATH"
fi
echo ""
echo "Next steps:"
if [[ -n "$ES_SNAPSHOT_FILE" ]]; then
    echo "  1. Verify indices: curl -s ${ES_URL}/_cat/indices?v"
    echo "  2. Verify aliases: curl -s ${ES_URL}/_alias?pretty"
fi
echo "  3. Restart services: docker compose -f infra/dev/layer-10/compose.yaml restart"
echo ""
