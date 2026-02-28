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

cd "$AURORA_ROOT"

git fetch origin
git checkout "$GITHUB_BRANCH"
git pull --ff-only origin "$GITHUB_BRANCH"

source .venv/bin/activate
python -m pip install -r requirements.txt

echo "Repository updated to latest '$GITHUB_BRANCH'."

