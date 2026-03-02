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
SCENARIO_C_WORKFLOW_TEMPLATE="${SCENARIO_C_WORKFLOW_TEMPLATE:-$AURORA_ROOT/configs/workflows/scenario_c.workflow.template.json}"
SCENARIO_C_EXTRA_NODE_REPOS="${SCENARIO_C_EXTRA_NODE_REPOS:-}"

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

if [[ -n "$SCENARIO_C_EXTRA_NODE_REPOS" ]]; then
  IFS=',' read -r -a EXTRA_REPO_LIST <<< "$SCENARIO_C_EXTRA_NODE_REPOS"
  for repo in "${EXTRA_REPO_LIST[@]}"; do
    clean_repo="$(echo "$repo" | xargs)"
    if [[ -z "$clean_repo" ]]; then
      continue
    fi
    node_name="$(basename "$clean_repo")"
    node_name="${node_name%.git}"
    install_node "$clean_repo" "$node_name"
  done
fi

if [[ -f "$COMFYUI_ROOT/requirements.txt" ]]; then
  python3 -m pip install -r "$COMFYUI_ROOT/requirements.txt"
fi

if [[ -f "$CUSTOM_NODES_DIR/ComfyUI-TP-Blend/requirements.txt" ]]; then
  python3 -m pip install -r "$CUSTOM_NODES_DIR/ComfyUI-TP-Blend/requirements.txt"
fi

if [[ -f "$CUSTOM_NODES_DIR/ComfyUI_PS_Blend_Node/requirements.txt" ]]; then
  python3 -m pip install -r "$CUSTOM_NODES_DIR/ComfyUI_PS_Blend_Node/requirements.txt"
fi

if [[ ! -f "$SCENARIO_C_WORKFLOW_TEMPLATE" ]]; then
  echo "Scenario C workflow not found for node validation: $SCENARIO_C_WORKFLOW_TEMPLATE" >&2
  exit 1
fi

python3 - "$SCENARIO_C_WORKFLOW_TEMPLATE" "$COMFYUI_ROOT" <<'PY'
import json
import sys
from pathlib import Path

workflow_path = Path(sys.argv[1])
comfy_root = Path(sys.argv[2])
custom_nodes = comfy_root / "custom_nodes"

workflow = json.loads(workflow_path.read_text(encoding="utf-8"))

required_custom = {
    "Flux2MultiReference",
    "ImageStitch",
    "TPBlendAttentionProcessor",
    "ComfyUI_PS_Blend_Node",
}

class_types = {
    node.get("class_type")
    for node in workflow.values()
    if isinstance(node, dict) and isinstance(node.get("class_type"), str)
}

missing = []
for class_name in sorted(class_types & required_custom):
    found = False
    for candidate in custom_nodes.rglob("*.py"):
        try:
            text = candidate.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if class_name in text:
            found = True
            break
    if not found:
        missing.append(class_name)

if missing:
    print("Missing Scenario C required custom node classes:", file=sys.stderr)
    for class_name in missing:
        print(f"  - {class_name}", file=sys.stderr)
    print(
        "Install/declare additional repositories via SCENARIO_C_EXTRA_NODE_REPOS in env and retry.",
        file=sys.stderr,
    )
    sys.exit(1)

print("Scenario C custom node validation passed.")
PY

echo "Custom node installation completed."
