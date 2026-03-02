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

require_env_key "AURORA_ROOT"
require_env_key "DATA_ROOT"
require_env_key "MODELS_ROOT"
require_env_key "OUTPUTS_ROOT"
require_env_key "MANIFESTS_ROOT"
require_env_key "GITHUB_REPO_URL"
require_env_key "GITHUB_BRANCH"
require_env_key "COMFYUI_ROOT"

if (( ${#missing_keys[@]} > 0 )); then
  echo "Bootstrap preflight failed. Missing or unresolved required env keys:" >&2
  for key in "${missing_keys[@]}"; do
    echo "  - $key" >&2
  done
  echo "Fix $ENV_FILE before retrying." >&2
  exit 1
fi

COMFYUI_PORT="${COMFYUI_PORT:-8188}"
COMFYUI_REPO_URL="${COMFYUI_REPO_URL:-https://github.com/comfyanonymous/ComfyUI.git}"

sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  git git-lfs jq curl unzip python3 python3-venv python3-pip

sudo mkdir -p "$AURORA_ROOT" "$DATA_ROOT" "$MODELS_ROOT" "$OUTPUTS_ROOT" "$MANIFESTS_ROOT" /opt/aurora/logs
sudo chown -R "$USER":"$USER" /opt/aurora

git lfs install || true

if [[ ! -d "$AURORA_ROOT/.git" ]]; then
  git clone --branch "${GITHUB_BRANCH}" "${GITHUB_REPO_URL}" "$AURORA_ROOT"
else
  echo "Repository already exists at $AURORA_ROOT. Updating to ${GITHUB_BRANCH}..."
  git -C "$AURORA_ROOT" fetch origin
  git -C "$AURORA_ROOT" checkout "$GITHUB_BRANCH"
  git -C "$AURORA_ROOT" pull --ff-only origin "$GITHUB_BRANCH"
fi

cd "$AURORA_ROOT"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

mkdir -p "$HF_HOME"

if [[ ! -d "$COMFYUI_ROOT/.git" ]]; then
  git clone "$COMFYUI_REPO_URL" "$COMFYUI_ROOT"
else
  git -C "$COMFYUI_ROOT" pull --ff-only
fi

if [[ -f "$COMFYUI_ROOT/requirements.txt" ]]; then
  python -m pip install -r "$COMFYUI_ROOT/requirements.txt"
fi

"$AURORA_ROOT/scripts/deploy/install_custom_nodes.sh" "$ENV_FILE"

SERVICE_TEMPLATE="$AURORA_ROOT/scripts/deploy/comfyui.service"
SERVICE_FILE="/etc/systemd/system/comfyui.service"

if [[ ! -f "$SERVICE_TEMPLATE" ]]; then
  echo "Missing service template: $SERVICE_TEMPLATE" >&2
  exit 1
fi

TMP_SERVICE_FILE="$(mktemp)"
sed \
  -e "s|__SERVICE_USER__|$USER|g" \
  -e "s|__AURORA_ROOT__|$AURORA_ROOT|g" \
  -e "s|__COMFYUI_ROOT__|$COMFYUI_ROOT|g" \
  -e "s|__COMFYUI_PORT__|$COMFYUI_PORT|g" \
  "$SERVICE_TEMPLATE" > "$TMP_SERVICE_FILE"

sudo cp "$TMP_SERVICE_FILE" "$SERVICE_FILE"
rm -f "$TMP_SERVICE_FILE"

sudo systemctl daemon-reload
sudo systemctl enable --now comfyui.service

echo "Verifying ComfyUI service health on 127.0.0.1:${COMFYUI_PORT}..."
service_ready=0
for _ in $(seq 1 45); do
  if curl -fsS "http://127.0.0.1:${COMFYUI_PORT}/system_stats" >/dev/null 2>&1; then
    service_ready=1
    break
  fi
  sleep 2
done

if [[ "$service_ready" -ne 1 ]]; then
  echo "ComfyUI service failed readiness check." >&2
  sudo systemctl --no-pager --full status comfyui.service || true
  exit 1
fi

cat <<EOF
Bootstrap completed.
ComfyUI service is enabled and healthy on 127.0.0.1:${COMFYUI_PORT}.
Next: run pipeline commands from $AURORA_ROOT using $ENV_FILE.
EOF

