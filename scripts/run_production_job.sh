#!/usr/bin/env bash
# =============================================================================
# VidGen — Execute a production film generation run as a Cloud Run Job.
#
# Creates a new FilmProject via the API, then triggers the Cloud Run Job
# worker with VIDGEN_PROJECT_ID set to the new project's ID.
#
# Usage:
#   source .env
#   ./scripts/run_production_job.sh [TOPIC]
#
# If TOPIC is omitted, the Ghost of Ithaca default topic is used.
# =============================================================================
set -euo pipefail

: "${GOOGLE_CLOUD_PROJECT:?Need GOOGLE_CLOUD_PROJECT}"
: "${GCS_BUCKET:?Need GCS_BUCKET}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
SERVICE_NAME="${CLOUD_RUN_SERVICE:-vidgen-api}"
JOB_NAME="${CLOUD_RUN_JOB_NAME:-vidgen-worker}"
TOPIC="${1:-GHOST OF ITHACA: A mythic psychological drama. Odysseus returns to Ithaca after twenty years.}"

# ── Resolve API URL ───────────────────────────────────────────────────────────
API_URL="${VIDGEN_API_URL:-}"
if [ -z "${API_URL}" ]; then
  API_URL=$(gcloud run services describe "${SERVICE_NAME}" \
    --region "${REGION}" \
    --project "${GOOGLE_CLOUD_PROJECT}" \
    --format "value(status.url)")
fi
echo "[RUN] API: ${API_URL}"

# ── Create project via API ────────────────────────────────────────────────────
echo "[RUN] Creating new FilmProject..."
RESPONSE=$(curl -s -X POST "${API_URL}/api/v1/films" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -d "{
    \"topic\": \"${TOPIC}\",
    \"duration_seconds\": 48,
    \"genre\": \"psychological drama\",
    \"language\": \"English\",
    \"aspect_ratio\": \"16:9\",
    \"production_mode\": \"short_film\"
  }")

PROJECT_ID=$(echo "${RESPONSE}" | python3 -c "import sys,json; print(json.load(sys.stdin)['project_id'])")
echo "[RUN] Project ID: ${PROJECT_ID}"
echo "${PROJECT_ID}" > /tmp/vidgen_last_project_id.txt

# ── Trigger Cloud Run Job with project ID ─────────────────────────────────────
echo "[RUN] Triggering Cloud Run Job: ${JOB_NAME}..."
EXEC_NAME=$(gcloud run jobs execute "${JOB_NAME}" \
  --region "${REGION}" \
  --project "${GOOGLE_CLOUD_PROJECT}" \
  --update-env-vars "VIDGEN_PROJECT_ID=${PROJECT_ID}" \
  --wait \
  --format "value(metadata.name)" 2>&1 | tail -1)

echo "[RUN] Execution: ${EXEC_NAME}"
echo "[RUN] Project: ${PROJECT_ID}"
echo ""
echo "Monitor with:  ./scripts/monitor.sh ${PROJECT_ID}"
echo "Verify with:   ./scripts/verify_result.sh ${PROJECT_ID}"
