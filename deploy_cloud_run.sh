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

PROJECT_ID="cloud-run-projects-328620"
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
