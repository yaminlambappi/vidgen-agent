# VidGen — IAM Requirements

## Service Account

Create a dedicated service account for the Cloud Run runner:

```bash
gcloud iam service-accounts create vidgen-runner \
  --display-name "VidGen Production Runner" \
  --project ${GOOGLE_CLOUD_PROJECT}
```

## Required Roles

| Role | Purpose |
|---|---|
| `roles/storage.admin` | Read/write GCS bucket (state, assets, deliverables) |
| `roles/aiplatform.user` | Call Veo and Gemini via Vertex AI |
| `roles/run.invoker` | Allow Cloud Run Jobs to self-invoke (not required for external trigger) |
| `roles/logging.logWriter` | Write structured logs to Cloud Logging |
| `roles/cloudtrace.agent` | Write traces (optional but recommended) |

## Grant roles

```bash
SA="vidgen-runner@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com"

for ROLE in \
  roles/storage.admin \
  roles/aiplatform.user \
  roles/run.invoker \
  roles/logging.logWriter \
  roles/cloudtrace.agent; do
  gcloud projects add-iam-policy-binding "${GOOGLE_CLOUD_PROJECT}" \
    --member "serviceAccount:${SA}" \
    --role "${ROLE}"
done
```

## GCS Bucket

```bash
gsutil mb -p ${GOOGLE_CLOUD_PROJECT} -l ${GOOGLE_CLOUD_LOCATION} gs://${GCS_BUCKET}

# Allow the service account full access to the bucket
gsutil iam ch serviceAccount:${SA}:roles/storage.admin gs://${GCS_BUCKET}
```

## Required APIs

Enable these APIs in the project:

```bash
gcloud services enable \
  run.googleapis.com \
  aiplatform.googleapis.com \
  storage.googleapis.com \
  texttospeech.googleapis.com \
  artifactregistry.googleapis.com \
  --project ${GOOGLE_CLOUD_PROJECT}
```

## Environment Variables (Cloud Run)

| Variable | Required | Example |
|---|---|---|
| `FILM_MODE` | Yes | `production` |
| `ALLOW_REAL_GENERATION` | Yes | `true` |
| `GOOGLE_CLOUD_PROJECT` | Yes | `vidgen-504817` |
| `GOOGLE_CLOUD_LOCATION` | Yes | `us-central1` |
| `GCS_BUCKET` | Yes | `vidgen-media-assets` |
| `VIDGEN_PROJECT_ID` | Yes (worker job only) | Set per-execution via `--update-env-vars` |
| `VEO_MODEL` | No | `veo-3.1-generate-001` |
| `DIRECTOR_MODEL` | No | `gemini-2.5-flash` |
| `IMAGE_MODEL` | No | `gemini-2.5-flash-image` |
| `TTS_VOICE` | No | `en-US-Neural2-J` |
| `SHOTS_PER_SCENE` | No | `2` |
| `MAX_SHOTS` | No | `42` |
| `RETRY_ATTEMPTS` | No | `3` |
| `VEO_TIMEOUT_SECONDS` | No | `1800` |
| `CLOUD_RUN_JOB_NAME` | Yes (API) | `vidgen-worker` |
