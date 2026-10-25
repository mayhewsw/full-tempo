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
SERVICE="full-tempo"

gcloud config set project "$PROJECT_ID"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com

# --timeout: the build context includes the ~1.8GB baked-in SQLite DB (see
# data/README.md), so the default 10-minute Cloud Build timeout can be tight
# depending on upload bandwidth.
gcloud builds submit --tag "gcr.io/$PROJECT_ID/$SERVICE" --timeout=20m

# --memory: the image bakes in a ~1.8GB SQLite file (data/musicbrainz_classical.sqlite,
# see data/README.md) that's queried read-only via OS page cache. 4Gi gives enough
# headroom to keep that fully cached plus Python/Flask overhead and a few concurrent
# requests. Not setting --min-instances: this stays scale-to-zero (no idle billing),
# at the cost of a slower cold start since Cloud Run has to pull the ~2GB image first —
# fine for a low-traffic personal app; revisit if cold starts become annoying.
gcloud run deploy "$SERVICE" \
  --region "$REGION" \
  --image "gcr.io/$PROJECT_ID/$SERVICE" \
  --memory 4Gi \
  --allow-unauthenticated \
  --port 8080

echo
echo "Deployed. Service URL:"
gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)'
