#!/usr/bin/env bash
# =============================================================================
# VidGen — One-command production deployment to Cloud Run (API) + Cloud Run Job
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
JOB_NAME="${CLOUD_RUN_JOB_NAME:-vidgen-worker}"
IMAGE="gcr.io/${GOOGLE_CLOUD_PROJECT}/vidgen:latest"
SA="${SERVICE_ACCOUNT_EMAIL:-vidgen-runner@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com}"

echo "================================================================"
echo "VidGen Deploy"
echo "  project  : ${GOOGLE_CLOUD_PROJECT}"
echo "  region   : ${REGION}"
echo "  image    : ${IMAGE}"
echo "  service  : ${SERVICE_NAME}"
echo "  job      : ${JOB_NAME}"
echo "================================================================"

# ── 1. Build & push image ────────────────────────────────────────────────────
echo "[1/4] Building Docker image..."
docker build --platform linux/amd64 -t "${IMAGE}" .
echo "[1/4] Pushing image..."
docker push "${IMAGE}"

# ── 2. Deploy API (Cloud Run Service) ───────────────────────────────────────
echo "[2/4] Deploying API service: ${SERVICE_NAME}..."
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

# ── 3. Deploy Worker (Cloud Run Job) ─────────────────────────────────────────
echo "[3/4] Deploying worker job: ${JOB_NAME}..."
gcloud run jobs update "${JOB_NAME}" \
  --image "${IMAGE}" \
  --region "${REGION}" \
  --project "${GOOGLE_CLOUD_PROJECT}" \
  --service-account "${SA}" \
  --set-env-vars "FILM_MODE=production,ALLOW_REAL_GENERATION=true,GOOGLE_CLOUD_PROJECT=${GOOGLE_CLOUD_PROJECT},GCS_BUCKET=${GCS_BUCKET},GOOGLE_CLOUD_LOCATION=${REGION}" \
  --memory 4Gi \
  --cpu 4 \
  --task-timeout 7200 \
  --max-retries 1 \
  --command "python" \
  --args "run_production.py" \
  || gcloud run jobs create "${JOB_NAME}" \
      --image "${IMAGE}" \
      --region "${REGION}" \
      --project "${GOOGLE_CLOUD_PROJECT}" \
      --service-account "${SA}" \
      --set-env-vars "FILM_MODE=production,ALLOW_REAL_GENERATION=true,GOOGLE_CLOUD_PROJECT=${GOOGLE_CLOUD_PROJECT},GCS_BUCKET=${GCS_BUCKET},GOOGLE_CLOUD_LOCATION=${REGION}" \
      --memory 4Gi \
      --cpu 4 \
      --task-timeout 7200 \
      --max-retries 1 \
      --command "python" \
      --args "run_production.py"

# ── 4. Print service URL ──────────────────────────────────────────────────────
echo "[4/4] Deployment complete."
SVC_URL=$(gcloud run services describe "${SERVICE_NAME}" \
  --region "${REGION}" \
  --project "${GOOGLE_CLOUD_PROJECT}" \
  --format "value(status.url)")
echo "  API URL   : ${SVC_URL}"
echo "  Health    : ${SVC_URL}/health"
echo "  Job name  : ${JOB_NAME}"
echo "================================================================"
