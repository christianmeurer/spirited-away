# Lightning AI VM Deployment (Minimal Additive Path)

This runbook uses the Lightning-specific bootstrap script while keeping the existing DigitalOcean deployment flow unchanged.

## 1) Clone repository on the Lightning VM

git clone https://github.com/YOUR_ORG/spirited-away.git /teamspace/studios/this_studio/spirited-away
cd /teamspace/studios/this_studio/spirited-away

## 2) Prepare env file and inject secrets

cp configs/env/lightning_ai.env.example configs/env/lightning_ai.env

# Edit non-secret path/runtime values as needed.
# Inject secrets via Lightning Secrets/UI (recommended) or export in shell session:
# export HF_TOKEN='hf_xxx'

## 3) Bootstrap Lightning VM runtime and ComfyUI API

chmod +x scripts/deploy/bootstrap_lightning.sh
./scripts/deploy/bootstrap_lightning.sh configs/env/lightning_ai.env

# Optional: request virtualenv creation (falls back to current env if Studio disallows it)
# LIGHTNING_CREATE_VENV=1 ./scripts/deploy/bootstrap_lightning.sh configs/env/lightning_ai.env

## 4) Run full pipeline (default: A/B smooth, Scenario C auto-skip if incomplete)

python scripts/pipeline/run_full_pipeline.py --env-file configs/env/lightning_ai.env --scenario all --dry-run-training

## 5) Optional strict Scenario C mode (fail if Scenario C contract/assets are incomplete)

# Ensure Scenario C assets exist under ${COMFYUI_ROOT}/input or use absolute paths in env.
python scripts/pipeline/run_full_pipeline.py --env-file configs/env/lightning_ai.env --scenario all --require-scenario-c --dry-run-training

## Notes

- Use Lightning persistent storage paths (for example under `/teamspace/studios/this_studio`) for `DATA_ROOT`, `MODELS_ROOT`, `OUTPUTS_ROOT`, `MANIFESTS_ROOT`, and `HF_HOME`.
- Do not commit secrets. Keep `HF_TOKEN` injected at runtime (Lightning Secrets/environment variables).
- Bootstrap uses the current Studio Python/conda environment by default and installs dependencies with `python -m pip`.
- ComfyUI is started in background by the bootstrap script and logged to `${AURORA_ROOT}/.runtime/comfyui.log`.

## Troubleshooting: Permission denied on `.git/FETCH_HEAD`

If bootstrap logs `cannot open '.git/FETCH_HEAD': Permission denied`, the script now warns and continues with the existing checkout.

If bootstrap logs `install_custom_nodes.sh: Permission denied`, bootstrap now attempts `chmod +x` and then runs the installer via `bash`, warning and continuing if installer execution still fails.

Inspect current user and permissions (Lightning Studio, no sudo):

```bash
whoami
id
ls -ld /teamspace/studios/this_studio/spirited-away
ls -ld /teamspace/studios/this_studio/spirited-away/.git
ls -l /teamspace/studios/this_studio/spirited-away/.git/FETCH_HEAD 2>/dev/null || true
```

Try a safe in-place permission repair for your current user:

```bash
chmod -R u+rwX /teamspace/studios/this_studio/spirited-away/.git
```

If ownership is mismatched and the error persists, re-clone as the active Studio user:

```bash
ts=$(date +%Y%m%d%H%M%S)
mv /teamspace/studios/this_studio/spirited-away /teamspace/studios/this_studio/spirited-away.bak.$ts
git clone https://github.com/YOUR_ORG/spirited-away.git /teamspace/studios/this_studio/spirited-away
```

