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

sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  git jq curl unzip python3 python3-venv python3-pip

sudo mkdir -p "$AURORA_ROOT" "$DATA_ROOT" "$MODELS_ROOT" "$OUTPUTS_ROOT" "$MANIFESTS_ROOT"
sudo chown -R "$USER":"$USER" /opt/aurora

if [[ ! -d "$AURORA_ROOT/.git" ]]; then
  git clone --branch "${GITHUB_BRANCH}" "${GITHUB_REPO_URL}" "$AURORA_ROOT"
else
  echo "Repository already exists at $AURORA_ROOT"
fi

cd "$AURORA_ROOT"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

mkdir -p "$HF_HOME"

cat <<EOF
Bootstrap completed.
Next:
1) Copy configs/env/digitalocean_h100.env.example -> configs/env/digitalocean_h100.env and fill secrets.
2) Export HF token: export HF_TOKEN=...
3) Start ComfyUI API bound to 127.0.0.1:8188.
EOF

