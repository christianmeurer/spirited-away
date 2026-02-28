#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-configs/env/digitalocean_h100.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Env file not found: $ENV_FILE" >&2
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

if ! command -v doctl >/dev/null 2>&1; then
  echo "doctl is required on the control machine." >&2
  exit 1
fi

: "${DO_DROPLET_NAME:?DO_DROPLET_NAME is required}"
: "${DO_REGION:?DO_REGION is required}"
: "${DO_IMAGE:?DO_IMAGE is required}"
: "${DO_SIZE:?DO_SIZE is required}"
: "${DO_SSH_FINGERPRINT:?DO_SSH_FINGERPRINT is required}"

echo "Creating droplet '$DO_DROPLET_NAME' in region '$DO_REGION' with size '$DO_SIZE'..."

doctl compute droplet create "$DO_DROPLET_NAME" \
  --region "$DO_REGION" \
  --image "$DO_IMAGE" \
  --size "$DO_SIZE" \
  --ssh-keys "$DO_SSH_FINGERPRINT" \
  --enable-monitoring \
  --wait \
  --user-data-file scripts/deploy/cloud-init-aurora.yaml

echo "Droplet created. Fetching public IPv4..."
doctl compute droplet get "$DO_DROPLET_NAME" --format PublicIPv4 --no-header

