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

missing_keys=()

require_env_key() {
  local key="$1"
  local value="${!key:-}"
  if [[ -z "$value" ]]; then
    missing_keys+=("$key")
    return
  fi
  if [[ "$value" == REPLACE_WITH_* ]]; then
    missing_keys+=("$key (placeholder value detected)")
  fi
}

if ! command -v doctl >/dev/null 2>&1; then
  echo "doctl is required on the control machine." >&2
  exit 1
fi

require_env_key "DO_DROPLET_NAME"
require_env_key "DO_REGION"
require_env_key "DO_IMAGE"
require_env_key "DO_SIZE"
require_env_key "DO_SSH_FINGERPRINT"
require_env_key "GITHUB_REPO_URL"
require_env_key "GITHUB_BRANCH"
require_env_key "AURORA_ROOT"
require_env_key "COMFYUI_ROOT"

if (( ${#missing_keys[@]} > 0 )); then
  echo "Provision preflight failed. Missing or unresolved required env keys:" >&2
  for key in "${missing_keys[@]}"; do
    echo "  - $key" >&2
  done
  echo "Fix $ENV_FILE before retrying." >&2
  exit 1
fi

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

