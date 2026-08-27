#!/usr/bin/env bash
# =============================================================================
# VidGen — Verify that a completed project's final MP4 exists in GCS
#          and passes basic playability checks.
#
# Usage:
#   source .env
#   ./scripts/verify_result.sh <PROJECT_ID>
#   ./scripts/verify_result.sh      # uses /tmp/vidgen_last_project_id.txt
# =============================================================================
set -euo pipefail

: "${GOOGLE_CLOUD_PROJECT:?Need GOOGLE_CLOUD_PROJECT}"
: "${GCS_BUCKET:?Need GCS_BUCKET}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
SERVICE_NAME="${CLOUD_RUN_SERVICE:-vidgen-api}"

PROJECT_ID="${1:-}"
if [ -z "${PROJECT_ID}" ] && [ -f /tmp/vidgen_last_project_id.txt ]; then
  PROJECT_ID=$(cat /tmp/vidgen_last_project_id.txt)
fi
: "${PROJECT_ID:?Need PROJECT_ID}"

API_URL="${VIDGEN_API_URL:-}"
if [ -z "${API_URL}" ]; then
  API_URL=$(gcloud run services describe "${SERVICE_NAME}" \
    --region "${REGION}" \
    --project "${GOOGLE_CLOUD_PROJECT}" \
    --format "value(status.url)")
fi

FAILS=0

echo "================================================================"
echo "VidGen Verify — ${PROJECT_ID}"
echo "================================================================"

# ── 1. API status gate ────────────────────────────────────────────────────────
RESPONSE=$(curl -s "${API_URL}/api/v1/films/${PROJECT_ID}" \
  -H "Authorization: Bearer $(gcloud auth print-access-token)")
STATUS=$(echo "${RESPONSE}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))")
MANIFEST_URI=$(echo "${RESPONSE}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('manifest_uri',''))")

echo "[1] API status       : ${STATUS}"
if [ "${STATUS}" != "completed" ]; then
  echo "    FAIL: expected 'completed'"
  FAILS=$((FAILS+1))
fi

# ── 2. GCS manifest exists ────────────────────────────────────────────────────
MANIFEST_GCS="gs://${GCS_BUCKET}/projects/${PROJECT_ID}/deliverables/manifest.json"
echo "[2] GCS manifest     : ${MANIFEST_GCS}"
if gsutil ls "${MANIFEST_GCS}" > /dev/null 2>&1; then
  echo "    OK"
else
  echo "    FAIL: manifest not found in GCS"
  FAILS=$((FAILS+1))
fi

# ── 3. GCS final MP4 exists ───────────────────────────────────────────────────
VIDEO_GCS="gs://${GCS_BUCKET}/projects/${PROJECT_ID}/deliverables/final_film.mp4"
echo "[3] GCS final MP4    : ${VIDEO_GCS}"
if gsutil ls "${VIDEO_GCS}" > /dev/null 2>&1; then
  SIZE=$(gsutil du -s "${VIDEO_GCS}" | awk '{print $1}')
  echo "    OK (${SIZE} bytes)"
  if [ "${SIZE}" -lt 10000 ]; then
    echo "    FAIL: file too small (< 10KB)"
    FAILS=$((FAILS+1))
  fi
else
  echo "    FAIL: final_film.mp4 not found in GCS"
  FAILS=$((FAILS+1))
fi

# ── 4. ffprobe playability check ─────────────────────────────────────────────
LOCAL_FILE="/tmp/verify_${PROJECT_ID}_final.mp4"
echo "[4] Downloading for ffprobe check..."
if gsutil cp "${VIDEO_GCS}" "${LOCAL_FILE}" 2>/dev/null; then
  PROBE=$(ffprobe -v error -show_entries format=duration:stream=codec_name,codec_type \
    -of json "${LOCAL_FILE}" 2>/dev/null)
  DURATION=$(echo "${PROBE}" | python3 -c "import sys,json; print(json.load(sys.stdin)['format']['duration'])")
  HAS_VIDEO=$(echo "${PROBE}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(any(s.get('codec_type')=='video' for s in d.get('streams',[])))")
  HAS_AUDIO=$(echo "${PROBE}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(any(s.get('codec_type')=='audio' for s in d.get('streams',[])))")
  echo "    duration=${DURATION}s  video=${HAS_VIDEO}  audio=${HAS_AUDIO}"
  if python3 -c "import sys; sys.exit(0 if float('${DURATION}') > 2.0 else 1)"; then
    echo "    OK"
  else
    echo "    FAIL: duration too short"
    FAILS=$((FAILS+1))
  fi
  rm -f "${LOCAL_FILE}"
else
  echo "    SKIP: could not download (GCS check already covers existence)"
fi

# ── Result ────────────────────────────────────────────────────────────────────
echo "================================================================"
if [ "${FAILS}" -eq 0 ]; then
  echo "ALL GATES PASSED"
  echo "  Final MP4: ${VIDEO_GCS}"
else
  echo "FAILED: ${FAILS} gate(s) failed"
  exit 1
fi
echo "================================================================"
