#!/usr/bin/env bash
# =============================================================================
# VidGen — Monitor a running production film project until completion.
#
# Usage:
#   source .env
#   ./scripts/monitor.sh <PROJECT_ID>
#   ./scripts/monitor.sh          # uses /tmp/vidgen_last_project_id.txt
# =============================================================================
set -euo pipefail

: "${GOOGLE_CLOUD_PROJECT:?Need GOOGLE_CLOUD_PROJECT}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
SERVICE_NAME="${CLOUD_RUN_SERVICE:-vidgen-api}"

PROJECT_ID="${1:-}"
if [ -z "${PROJECT_ID}" ] && [ -f /tmp/vidgen_last_project_id.txt ]; then
  PROJECT_ID=$(cat /tmp/vidgen_last_project_id.txt)
fi
: "${PROJECT_ID:?Need PROJECT_ID as argument or in /tmp/vidgen_last_project_id.txt}"

API_URL="${VIDGEN_API_URL:-}"
if [ -z "${API_URL}" ]; then
  API_URL=$(gcloud run services describe "${SERVICE_NAME}" \
    --region "${REGION}" \
    --project "${GOOGLE_CLOUD_PROJECT}" \
    --format "value(status.url)")
fi

echo "================================================================"
echo "VidGen Monitor — ${PROJECT_ID}"
echo "API: ${API_URL}"
echo "================================================================"

while true; do
  RESPONSE=$(curl -s "${API_URL}/api/v1/films/${PROJECT_ID}" \
    -H "Authorization: Bearer $(gcloud auth print-access-token)" 2>/dev/null || echo '{"status":"error"}')
  STATUS=$(echo "${RESPONSE}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','?'))")
  PROGRESS=$(echo "${RESPONSE}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('progress','?'))")
  TIMESTAMP=$(date '+%H:%M:%S')

  echo "[${TIMESTAMP}] status=${STATUS} progress=${PROGRESS}%"

  if [ "${STATUS}" = "completed" ]; then
    URI=$(echo "${RESPONSE}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('final_video_uri',''))")
    echo ""
    echo "================================================================"
    echo "COMPLETED"
    echo "  Final MP4: ${URI}"
    echo "================================================================"
    break
  elif [ "${STATUS}" = "failed" ]; then
    ERROR=$(echo "${RESPONSE}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('error','unknown'))")
    echo ""
    echo "================================================================"
    echo "FAILED: ${ERROR}"
    echo "================================================================"
    exit 1
  elif [ "${STATUS}" = "error" ]; then
    echo "[WARN] Could not reach API — will retry..."
  fi

  sleep 15
done
