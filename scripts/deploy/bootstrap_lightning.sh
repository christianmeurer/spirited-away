#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-configs/env/lightning_ai.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Env file not found: $ENV_FILE" >&2
  echo "Create it from configs/env/lightning_ai.env.example before retrying." >&2
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

required_keys=(
  "AURORA_ROOT"
  "DATA_ROOT"
  "MODELS_ROOT"
  "OUTPUTS_ROOT"
  "MANIFESTS_ROOT"
  "HF_HOME"
  "COMFYUI_ROOT"
)

missing_keys=()
for key in "${required_keys[@]}"; do
  value="${!key:-}"
  if [[ -z "$value" ]]; then
    missing_keys+=("$key")
  fi
done

if (( ${#missing_keys[@]} > 0 )); then
  echo "Bootstrap preflight failed. Missing required env keys:" >&2
  for key in "${missing_keys[@]}"; do
    echo "  - $key" >&2
  done
  exit 1
fi

COMFYUI_PORT="${COMFYUI_PORT:-8188}"
COMFYUI_BASE_URL="${COMFYUI_BASE_URL:-http://127.0.0.1:${COMFYUI_PORT}}"
COMFYUI_LISTEN_HOST="${COMFYUI_LISTEN_HOST:-0.0.0.0}"
COMFYUI_REPO_URL="${COMFYUI_REPO_URL:-https://github.com/comfyanonymous/ComfyUI.git}"
COMFYUI_STARTUP_TIMEOUT_SECONDS="${COMFYUI_STARTUP_TIMEOUT_SECONDS:-240}"
COMFYUI_RUNTIME_DIR="${COMFYUI_RUNTIME_DIR:-$AURORA_ROOT/.runtime}"
COMFYUI_LOG_FILE="${COMFYUI_LOG_FILE:-$COMFYUI_RUNTIME_DIR/comfyui.log}"
COMFYUI_PID_FILE="${COMFYUI_PID_FILE:-$COMFYUI_RUNTIME_DIR/comfyui.pid}"
LIGHTNING_CREATE_VENV="${LIGHTNING_CREATE_VENV:-0}"
LIGHTNING_VENV_PATH="${LIGHTNING_VENV_PATH:-$AURORA_ROOT/.venv}"

PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "python3/python is required but not found." >&2
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  echo "git is required but not found." >&2
  exit 1
fi

if command -v git-lfs >/dev/null 2>&1; then
  git lfs install || true
fi

print_git_permission_hint() {
  local repo_path="$1"
  local origin_url="$2"

  cat >&2 <<EOF
WARNING: Detected git permission issue for ${repo_path}.
Operator hint (Lightning Studio, no sudo):
  whoami
  id
  ls -ld "${repo_path}" "${repo_path}/.git" "${repo_path}/.git/FETCH_HEAD"
  ls -l "${repo_path}/.git/FETCH_HEAD" 2>/dev/null || true
  chmod -R u+rwX "${repo_path}/.git"
If ownership is wrong and chmod is insufficient, re-clone as the current Lightning user:
  ts=\$(date +%Y%m%d%H%M%S)
  mv "${repo_path}" "${repo_path}.bak.\$ts"
  git clone "${origin_url}" "${repo_path}"
EOF
}

resilient_git_update() {
  local repo_path="$1"
  local repo_label="$2"
  local fetch_output=""
  local pull_output=""
  local origin_url=""

  origin_url="$(git -C "$repo_path" remote get-url origin 2>/dev/null || echo "<repo-url>")"

  if ! fetch_output="$(git -C "$repo_path" fetch --all --tags 2>&1)"; then
    echo "WARNING: ${repo_label} git fetch failed; continuing with existing checkout." >&2
    echo "$fetch_output" >&2
    if [[ "$fetch_output" == *"Permission denied"* || "$fetch_output" == *"FETCH_HEAD"* ]]; then
      print_git_permission_hint "$repo_path" "$origin_url"
    fi
    return 0
  fi

  if ! pull_output="$(git -C "$repo_path" pull --ff-only 2>&1)"; then
    echo "WARNING: ${repo_label} git pull failed; continuing with existing checkout." >&2
    echo "$pull_output" >&2
    if [[ "$pull_output" == *"Permission denied"* || "$pull_output" == *"FETCH_HEAD"* ]]; then
      print_git_permission_hint "$repo_path" "$origin_url"
    fi
    return 0
  fi
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [[ "$AURORA_ROOT" != "$REPO_ROOT" ]]; then
  echo "AURORA_ROOT ($AURORA_ROOT) must match checked-out repository path ($REPO_ROOT) on Lightning VM." >&2
  exit 1
fi

mkdir -p "$AURORA_ROOT" "$DATA_ROOT" "$MODELS_ROOT" "$OUTPUTS_ROOT" "$MANIFESTS_ROOT" "$HF_HOME" "$COMFYUI_RUNTIME_DIR"

cd "$AURORA_ROOT"

resilient_git_update "$AURORA_ROOT" "Aurora repository"

USE_VENV=0
PIP_PYTHON_BIN="$PYTHON_BIN"
COMFYUI_PYTHON_BIN="$PYTHON_BIN"

case "${LIGHTNING_CREATE_VENV,,}" in
  1|true|yes|on)
    if "$PYTHON_BIN" -m venv "$LIGHTNING_VENV_PATH"; then
      if [[ -x "$LIGHTNING_VENV_PATH/bin/python" ]]; then
        USE_VENV=1
        PIP_PYTHON_BIN="$LIGHTNING_VENV_PATH/bin/python"
        COMFYUI_PYTHON_BIN="$LIGHTNING_VENV_PATH/bin/python"
        echo "Using virtualenv interpreter at $LIGHTNING_VENV_PATH/bin/python"
      else
        echo "WARNING: Virtualenv requested but interpreter not found at $LIGHTNING_VENV_PATH/bin/python; continuing with current environment." >&2
      fi
    else
      echo "WARNING: Virtualenv creation failed; continuing with current environment." >&2
    fi
    ;;
  *)
    echo "Using current Python environment ($PYTHON_BIN)."
    ;;
esac

"$PIP_PYTHON_BIN" -m pip install --upgrade pip
"$PIP_PYTHON_BIN" -m pip install -r requirements.txt

if [[ ! -d "$COMFYUI_ROOT/.git" ]]; then
  git clone "$COMFYUI_REPO_URL" "$COMFYUI_ROOT"
else
  resilient_git_update "$COMFYUI_ROOT" "ComfyUI checkout"
fi

if [[ -f "$COMFYUI_ROOT/requirements.txt" ]]; then
  "$PIP_PYTHON_BIN" -m pip install -r "$COMFYUI_ROOT/requirements.txt"
fi

CUSTOM_NODE_INSTALLER="$AURORA_ROOT/scripts/deploy/install_custom_nodes.sh"
if [[ -f "$CUSTOM_NODE_INSTALLER" && -f "$AURORA_ROOT/configs/workflows/scenario_c.workflow.template.json" ]]; then
  if ! chmod +x "$CUSTOM_NODE_INSTALLER" 2>/dev/null; then
    echo "WARNING: Unable to chmod +x $CUSTOM_NODE_INSTALLER; attempting execution via bash." >&2
  fi
  if ! bash "$CUSTOM_NODE_INSTALLER" "$ENV_FILE"; then
    echo "WARNING: Custom node installer failed; continuing bootstrap." >&2
  fi
else
  echo "Skipping custom node installer (incompatible or missing prerequisites)."
fi

comfy_healthcheck() {
  curl -fsS "${COMFYUI_BASE_URL%/}/system_stats" >/dev/null 2>&1
}

if [[ -f "$COMFYUI_PID_FILE" ]]; then
  existing_pid="$(cat "$COMFYUI_PID_FILE" 2>/dev/null || true)"
  if [[ -n "$existing_pid" ]] && kill -0 "$existing_pid" >/dev/null 2>&1; then
    if comfy_healthcheck; then
      echo "ComfyUI already healthy on ${COMFYUI_BASE_URL}."
      cat <<EOF
Bootstrap completed.
ComfyUI endpoint: ${COMFYUI_BASE_URL}
ComfyUI PID: ${existing_pid}
Log file: ${COMFYUI_LOG_FILE}
EOF
      exit 0
    fi
    kill "$existing_pid" || true
    sleep 2
  fi
  rm -f "$COMFYUI_PID_FILE"
fi

if comfy_healthcheck; then
  echo "ComfyUI already healthy on ${COMFYUI_BASE_URL}."
  cat <<EOF
Bootstrap completed.
ComfyUI endpoint: ${COMFYUI_BASE_URL}
Log file: ${COMFYUI_LOG_FILE}
EOF
  exit 0
fi

mkdir -p "$(dirname "$COMFYUI_LOG_FILE")"
nohup "$COMFYUI_PYTHON_BIN" "$COMFYUI_ROOT/main.py" \
  --listen "$COMFYUI_LISTEN_HOST" \
  --port "$COMFYUI_PORT" \
  >>"$COMFYUI_LOG_FILE" 2>&1 &

comfy_pid="$!"
echo "$comfy_pid" > "$COMFYUI_PID_FILE"

ready=0
for _ in $(seq 1 "$COMFYUI_STARTUP_TIMEOUT_SECONDS"); do
  if comfy_healthcheck; then
    ready=1
    break
  fi
  sleep 1
done

if [[ "$ready" -ne 1 ]]; then
  echo "ComfyUI failed readiness check at ${COMFYUI_BASE_URL}." >&2
  tail -n 100 "$COMFYUI_LOG_FILE" >&2 || true
  exit 1
fi

cat <<EOF
Bootstrap completed.
ComfyUI endpoint: ${COMFYUI_BASE_URL}
ComfyUI PID: ${comfy_pid}
Log file: ${COMFYUI_LOG_FILE}
EOF
