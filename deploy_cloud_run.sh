#!/usr/bin/env bash
set -euo pipefail

# Manual deploy to Cloud Run using Cloud Build (no local Docker required).
#
# Usage:
#   bash deploy_cloud_run.sh
#
# Prereqs:
#   - gcloud installed + authenticated (gcloud auth login)
#   - permissions to use Cloud Build + Cloud Run in the project

ENV_FILE="$(dirname "$0")/.env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: .env file not found at $ENV_FILE" >&2
  echo "Create it with: echo 'GCP_PROJECT_ID=your-project-id' > .env" >&2
  exit 1
fi
# shellcheck source=.env
source "$ENV_FILE"

PROJECT_ID="${GCP_PROJECT_ID:?GCP_PROJECT_ID is not set in .env}"
REGION="us-east1"
SERVICE="apple-music-distribution"

gcloud config set project "$PROJECT_ID"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com

gcloud builds submit --tag "gcr.io/$PROJECT_ID/$SERVICE"

gcloud run deploy "$SERVICE" \
  --region "$REGION" \
  --image "gcr.io/$PROJECT_ID/$SERVICE" \
  --set-env-vars "WORK_CACHE_GCS_BUCKET=apple-music-distribution-files,WORK_CACHE_TTL_SECONDS=0" \
  --allow-unauthenticated \
  --port 8080

echo
echo "Deployed. Service URL:"
gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)'
