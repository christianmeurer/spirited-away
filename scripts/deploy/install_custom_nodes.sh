#!/usr/bin/env bash
# Install and validate ComfyUI custom nodes for Aurora INTERNAL_RND.
#
# Node strategy:
#   1. Aurora-bundled nodes (ComfyUI-TP-Blend, ComfyUI-Aurora-Nodes) are
#      included in this repository under custom_nodes/ and are symlinked into
#      ComfyUI's custom_nodes directory.  No external git clone required.
#   2. PS Blend Node is cloned from its public GitHub repository.
#   3. Any additional Scenario C node repos may be declared via
#      SCENARIO_C_EXTRA_NODE_REPOS (comma-separated git URLs) in the env file.
#   4. After all nodes are in place, a Python validation pass verifies that
#      every class_type referenced by scenario_c.workflow.template.json is
#      present in at least one installed node's Python source.
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
PS_BLEND_NODE_REPO_URL="${PS_BLEND_NODE_REPO_URL:-https://github.com/bluevisor/ComfyUI_PS_Blend_Node.git}"
SCENARIO_C_WORKFLOW_TEMPLATE="${SCENARIO_C_WORKFLOW_TEMPLATE:-$AURORA_ROOT/configs/workflows/scenario_c.workflow.template.json}"
SCENARIO_C_EXTRA_NODE_REPOS="${SCENARIO_C_EXTRA_NODE_REPOS:-}"

mkdir -p "$CUSTOM_NODES_DIR"

# ---------------------------------------------------------------------------
# 1. Link Aurora-bundled custom nodes from the repository
# ---------------------------------------------------------------------------

link_bundled_node() {
  local node_name="$1"
  local src="$AURORA_ROOT/custom_nodes/$node_name"
  local dst="$CUSTOM_NODES_DIR/$node_name"

  if [[ ! -d "$src" ]]; then
    echo "Bundled custom node source not found: $src" >&2
    exit 1
  fi

  if [[ -L "$dst" ]]; then
    echo "Bundled node already linked: $node_name"
    return 0
  fi

  if [[ -d "$dst" && ! -L "$dst" ]]; then
    echo "WARNING: $dst exists as a real directory (not a symlink). Skipping link for $node_name."
    return 0
  fi

  ln -sfn "$src" "$dst"
  echo "Linked bundled node: $node_name -> $dst"
}

link_bundled_node "ComfyUI-TP-Blend"
link_bundled_node "ComfyUI-Aurora-Nodes"

# ---------------------------------------------------------------------------
# 2. Clone/update external nodes
# ---------------------------------------------------------------------------

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

install_node "$PS_BLEND_NODE_REPO_URL" "ComfyUI_PS_Blend_Node"

# Optional extra repos declared in env
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

# ---------------------------------------------------------------------------
# 3. Install Python dependencies for all custom nodes
# ---------------------------------------------------------------------------

if [[ -f "$COMFYUI_ROOT/requirements.txt" ]]; then
  python3 -m pip install -r "$COMFYUI_ROOT/requirements.txt"
fi

for node_dir in "$CUSTOM_NODES_DIR"/*/; do
  req="$node_dir/requirements.txt"
  if [[ -f "$req" ]]; then
    echo "Installing requirements for: $(basename "$node_dir")"
    python3 -m pip install -r "$req"
  fi
done

# ---------------------------------------------------------------------------
# 4. Validate that all class_types referenced by Scenario C are resolvable
# ---------------------------------------------------------------------------

if [[ ! -f "$SCENARIO_C_WORKFLOW_TEMPLATE" ]]; then
  echo "Scenario C workflow not found for node validation: $SCENARIO_C_WORKFLOW_TEMPLATE" >&2
  exit 1
fi

python3 - "$SCENARIO_C_WORKFLOW_TEMPLATE" "$COMFYUI_ROOT" <<'PY'
import json
import os
import sys
from pathlib import Path

workflow_path = Path(sys.argv[1])
comfy_root = Path(sys.argv[2])
custom_nodes = comfy_root / "custom_nodes"

workflow = json.loads(workflow_path.read_text(encoding="utf-8"))

# Custom class_types that Scenario C requires from non-standard nodes.
# Standard ComfyUI built-in nodes (UNETLoader, DualCLIPLoader, VAELoader,
# CLIPTextEncode, FluxGuidance, EmptyLatentImage, KSampler, VAEDecode,
# VAEEncode, LoadImage, SaveImage) are NOT listed here.
required_custom = {
    "Flux2MultiReference",
    "ImageStitch",
    "TPBlendAttentionProcessor",
    "PSBlendNode",
}

class_types = {
    node.get("class_type")
    for node in workflow.values()
    if isinstance(node, dict) and isinstance(node.get("class_type"), str)
}

# Only validate class_types that are actually in the workflow
to_check = class_types & required_custom

missing = []
for class_name in sorted(to_check):
    found = False
    for root, _, files in os.walk(custom_nodes, followlinks=True):
        for filename in files:
            if not filename.endswith(".py"):
                continue
            candidate = Path(root) / filename
            try:
                text = candidate.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            # Check that the class name is registered in NODE_CLASS_MAPPINGS
            if f'"{class_name}"' in text or f"'{class_name}'" in text:
                found = True
                break
        if found:
            break
    if not found:
        missing.append(class_name)

if missing:
    print("Missing Scenario C required custom node classes:", file=sys.stderr)
    for class_name in missing:
        print(f"  - {class_name}", file=sys.stderr)
    print(
        "\nFor custom external nodes, add their repository URLs to "
        "SCENARIO_C_EXTRA_NODE_REPOS in the env file (comma-separated) and retry.",
        file=sys.stderr,
    )
    sys.exit(1)

print(f"Scenario C custom node validation passed. Checked: {sorted(to_check)}")
PY

echo "Custom node installation completed."
