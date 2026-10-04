#!/usr/bin/env bash
# =============================================================================
# School of Sufi — deploy the /generate API to Cloud Run
#
# Prerequisites:
#   gcloud CLI authenticated with a principal that has:
#     roles/run.admin, roles/iam.serviceAccountUser,
#     roles/storage.admin, roles/artifactregistry.repoAdmin
#   GOOGLE_CLOUD_PROJECT and GCS_BUCKET env vars set (or sourced from .env)
#
# Usage:
#   source .env           # populate GOOGLE_CLOUD_PROJECT, GCS_BUCKET, etc.
#   ./scripts/deploy.sh
# =============================================================================
set -euo pipefail

: "${GOOGLE_CLOUD_PROJECT:?Need GOOGLE_CLOUD_PROJECT}"
: "${GCS_BUCKET:?Need GCS_BUCKET}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
SERVICE_NAME="${CLOUD_RUN_SERVICE:-vidgen-api}"
IMAGE="gcr.io/${GOOGLE_CLOUD_PROJECT}/vidgen:latest"
SA="${SERVICE_ACCOUNT_EMAIL:-vidgen-runner@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com}"

echo "================================================================"
echo "School of Sufi"
echo "  project  : ${GOOGLE_CLOUD_PROJECT}"
echo "  region   : ${REGION}"
echo "  image    : ${IMAGE}"
echo "  service  : ${SERVICE_NAME}"
echo "================================================================"

# ── 1. Build & push image ────────────────────────────────────────────────────
echo "[1/3] Building Docker image..."
docker build --platform linux/amd64 -t "${IMAGE}" .
echo "[1/3] Pushing image..."
docker push "${IMAGE}"

# ── 2. Deploy API (Cloud Run Service) ───────────────────────────────────────
echo "[2/3] Deploying API service: ${SERVICE_NAME}..."
gcloud run deploy "${SERVICE_NAME}" \
  --image "${IMAGE}" \
  --platform managed \
  --region "${REGION}" \
  --project "${GOOGLE_CLOUD_PROJECT}" \
  --service-account "${SA}" \
  --set-env-vars "FILM_MODE=production,ALLOW_REAL_GENERATION=true,GOOGLE_CLOUD_PROJECT=${GOOGLE_CLOUD_PROJECT},GCS_BUCKET=${GCS_BUCKET},GOOGLE_CLOUD_LOCATION=${REGION}" \
  --memory 2Gi \
  --cpu 2 \
  --timeout 3600 \
  --max-instances 5 \
  --allow-unauthenticated

# ── 3. Print service URL ──────────────────────────────────────────────────────
echo "[3/3] Deployment complete."
SVC_URL=$(gcloud run services describe "${SERVICE_NAME}" \
  --region "${REGION}" \
  --project "${GOOGLE_CLOUD_PROJECT}" \
  --format "value(status.url)")
echo "  API URL   : ${SVC_URL}"
echo "  Health    : ${SVC_URL}/health"
echo "  Generate  : ${SVC_URL}/generate"
echo "================================================================"
