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

: "${AURORA_ROOT:?AURORA_ROOT is required}"

COMFYUI_ROOT="${COMFYUI_ROOT:-/opt/aurora/ComfyUI}"
CUSTOM_NODES_DIR="$COMFYUI_ROOT/custom_nodes"
TP_BLEND_NODE_REPO_URL="${TP_BLEND_NODE_REPO_URL:-https://github.com/felixxinjin1/TP-Blend.git}"
PS_BLEND_NODE_REPO_URL="${PS_BLEND_NODE_REPO_URL:-https://github.com/bluevisor/ComfyUI_PS_Blend_Node.git}"

mkdir -p "$CUSTOM_NODES_DIR"

if [[ "$TP_BLEND_NODE_REPO_URL" == *"example-org"* ]] || [[ "$PS_BLEND_NODE_REPO_URL" == *"example-org"* ]]; then
  echo "TP_BLEND_NODE_REPO_URL and PS_BLEND_NODE_REPO_URL must be set to real repositories in env file." >&2
  exit 1
fi

install_node() {
  local url="$1"
  local name="$2"
  local dst="$CUSTOM_NODES_DIR/$name"

  if [[ -d "$dst/.git" ]]; then
    echo "Updating custom node: $name"
    git -C "$dst" pull --ff-only
  else
    echo "Installing custom node: $name"
    git clone "$url" "$dst"
  fi
}

# TP-Blend processor and ComfyUI PS Blend node integration
install_node "$TP_BLEND_NODE_REPO_URL" "ComfyUI-TP-Blend"
install_node "$PS_BLEND_NODE_REPO_URL" "ComfyUI_PS_Blend_Node"

if [[ -f "$COMFYUI_ROOT/requirements.txt" ]]; then
  python3 -m pip install -r "$COMFYUI_ROOT/requirements.txt"
fi

if [[ -f "$CUSTOM_NODES_DIR/ComfyUI-TP-Blend/requirements.txt" ]]; then
  python3 -m pip install -r "$CUSTOM_NODES_DIR/ComfyUI-TP-Blend/requirements.txt"
fi

if [[ -f "$CUSTOM_NODES_DIR/ComfyUI_PS_Blend_Node/requirements.txt" ]]; then
  python3 -m pip install -r "$CUSTOM_NODES_DIR/ComfyUI_PS_Blend_Node/requirements.txt"
fi

echo "Custom node installation completed."
