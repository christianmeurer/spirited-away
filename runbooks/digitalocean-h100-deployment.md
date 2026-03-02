# DigitalOcean H100 Deployment Architecture (Aurora Fotos INTERNAL_RND)

## Objective

Deploy a reproducible, internal-only image pipeline on DigitalOcean H100 GPU infrastructure for:

- Scenario A: Real person + photoreal companions
- Scenario B: Anime-stylized person + anime companions
- Scenario C: Mixed-media composition with strict domain boundaries

## Topology

1. **Control machine** (operator workstation)
   - Uses `doctl` + SSH to create and configure GPU droplet.
2. **DigitalOcean H100 droplet** (single-node execution)
   - Ubuntu 24.04 LTS
   - NVIDIA driver + CUDA runtime
   - Python runtime + pipeline scripts
   - ComfyUI API service (`127.0.0.1:8188`)
3. **DigitalOcean Spaces**
   - Internal artifact persistence (`spaces://...`)
4. **GitHub repository**
   - Source of truth for pipeline code and config

## Runtime Components

- Deployment scripts under `scripts/deploy/`
- Hugging Face model registry + fetcher under `configs/models/` and `scripts/models/`
- Identity dataset and training launcher under `scripts/training/`
- Character reference acquisition with license safeguards under `configs/characters/` and `scripts/assets/`
- Scenario orchestration under `scripts/pipeline/`
- Compliance validator under `scripts/internal_rnd_cli.py`

## Security and Policy Controls

- `USAGE_SCOPE=INTERNAL_RND`
- `SHARING_ALLOWED=false`
- Pre-export guard required before any artifact movement
- Public URL export blocked in validator
- Character source manifest requires rights metadata and approved source domain
- Model acquisition via pinned revisions and lock output

## Operational Sequence

1. Prepare env contract from [`configs/env/digitalocean_h100.env.example`](configs/env/digitalocean_h100.env.example) including `DO_SSH_FINGERPRINT` and Scenario C asset/mask keys.
2. Provision droplet with strict preflight validation.
3. Bootstrap VM with deterministic repo/runtime setup.
4. Install and validate Scenario C custom nodes from workflow references.
5. Enable and verify persistent ComfyUI API systemd service.
6. Acquire models from Hugging Face registry.
7. Acquire and validate character references from approved sources.
8. Prepare identity dataset and run adapter training.
9. Execute scenario generation pipeline (operational generation mode).
10. Run strict validation as an explicit step on generated manifests.
11. Run pre-export guard and archive to internal Spaces paths.

## Required Env Contract (Minimum)

`scripts/deploy/provision_do_h100.sh` now fails fast unless these keys are present and not unresolved placeholders:

- `DO_DROPLET_NAME`
- `DO_REGION`
- `DO_IMAGE`
- `DO_SIZE`
- `DO_SSH_FINGERPRINT`
- `GITHUB_REPO_URL`
- `GITHUB_BRANCH`
- `AURORA_ROOT`
- `COMFYUI_ROOT`

Scenario C generation contract keys (CLI can override):

- `SCENARIO_C_TRACK`
- `SCENARIO_C_SUBJECT_IMAGE`
- `SCENARIO_C_COMPANION_IMAGE_A`
- `SCENARIO_C_COMPANION_IMAGE_B`
- `SCENARIO_C_SUBJECT_MASK_IMAGE`
- `SCENARIO_C_COMPANION_MASK_IMAGE`
- `SCENARIO_C_PS_BLEND_MODE`

Optional archival keys:

- `ENABLE_SPACES_ARCHIVAL`
- `DO_SPACES_ACCESS_KEY_ID`
- `DO_SPACES_SECRET_ACCESS_KEY`
- `DO_SPACES_ARCHIVE_PREFIX`

## Implementation Commands

Control machine provisioning:

```bash
cp configs/env/digitalocean_h100.env.example configs/env/digitalocean_h100.env
# Fill placeholders before provisioning:
# - DO_SSH_FINGERPRINT
# - HF_TOKEN
# - Spaces keys if archival is enabled
./scripts/deploy/provision_do_h100.sh configs/env/digitalocean_h100.env
```

On VM bootstrap and repo setup:

```bash
cd /opt/aurora/Aurora-Fotos
./scripts/deploy/bootstrap_vm.sh configs/env/digitalocean_h100.env
./scripts/deploy/update_repo.sh configs/env/digitalocean_h100.env
```

Bootstrap now performs deterministic ComfyUI service setup using [`scripts/deploy/comfyui.service`](scripts/deploy/comfyui.service) and verifies readiness via `http://127.0.0.1:${COMFYUI_PORT:-8188}/system_stats`.

Service checks:

```bash
sudo systemctl status comfyui.service --no-pager
curl -fsS http://127.0.0.1:8188/system_stats
```

Install required mixed-media custom nodes (TP-Blend + PS Blend + optional `SCENARIO_C_EXTRA_NODE_REPOS`) and validate required Scenario C classes:

```bash
./scripts/deploy/install_custom_nodes.sh configs/env/digitalocean_h100.env
```

Acquire models from Hugging Face:

```bash
source .venv/bin/activate
python scripts/models/fetch_hf_models.py --env-file configs/env/digitalocean_h100.env --allow-optional-failures
```

Prepare dataset + optional training launch:

```bash
python scripts/training/prepare_identity_dataset.py \
  --input-dir Fotos-Aurora \
  --output-dir /opt/aurora/data/identity_dataset \
  --trigger-token "[subj_name_2026]"

python scripts/training/launch_identity_training.py \
  --env-file configs/env/digitalocean_h100.env \
  --dry-run
```

Acquire character references with rights checks:

```bash
python scripts/assets/acquire_character_refs.py \
  --source-manifest configs/characters/spirited_away_sources.internal_rnd.json \
  --output-dir /opt/aurora/data/character_refs
```

Run all scenarios with smooth defaults (`scenario_c` auto-skips if Scenario C contract/assets are incomplete):

```bash
python scripts/pipeline/run_scenarios.py \
  --env-file configs/env/digitalocean_h100.env \
  --scenario all
```

Enforce strict all-scenarios behavior (fail instead of skip when Scenario C contract/assets are incomplete):

```bash
python scripts/pipeline/run_scenarios.py \
  --env-file configs/env/digitalocean_h100.env \
  --scenario all \
  --require-scenario-c
```

Run Scenario C only with CLI overrides:

```bash
python scripts/pipeline/run_scenarios.py \
  --env-file configs/env/digitalocean_h100.env \
  --scenario scenario_c \
  --scenario-c-track track_c \
  --scenario-c-subject-image input/subject.png \
  --scenario-c-companion-image-a input/companion_a.png \
  --scenario-c-companion-image-b input/companion_b.png \
  --scenario-c-subject-mask-image input/subject_mask.png \
  --scenario-c-companion-mask-image input/companion_mask.png \
  --scenario-c-ps-blend-mode Multiply
```

Orchestrate full flow in one command (default keeps A/B smooth and auto-skips Scenario C if incomplete):

```bash
python scripts/pipeline/run_full_pipeline.py \
  --env-file configs/env/digitalocean_h100.env \
  --scenario all \
  --dry-run-training
```

Strict full-flow mode requiring Scenario C in all-mode:

```bash
python scripts/pipeline/run_full_pipeline.py \
  --env-file configs/env/digitalocean_h100.env \
  --scenario all \
  --require-scenario-c \
  --dry-run-training
```

Validate generated manifests:

```bash
python scripts/internal_rnd_cli.py validate --manifest manifests/generated/<run_id>_scenario_a.internal_rnd.json
python scripts/internal_rnd_cli.py validate --manifest manifests/generated/<run_id>_scenario_b.internal_rnd.json
python scripts/internal_rnd_cli.py validate --manifest manifests/generated/<run_id>_scenario_c.internal_rnd.json
```

Generation output manifests are not auto-approved. They remain pending manual approvals and human/metric scoring until [`scripts/internal_rnd_cli.py`](scripts/internal_rnd_cli.py) strict validation and review gates are completed.

Archive internal outputs to Spaces (runbook-promised path):

```bash
python scripts/pipeline/archive_to_spaces.py \
  --env-file configs/env/digitalocean_h100.env \
  --input manifests/generated
```

Dry-run archival preview:

```bash
python scripts/pipeline/archive_to_spaces.py \
  --env-file configs/env/digitalocean_h100.env \
  --input manifests/generated \
  --dry-run
```

## Failure Domains and Recovery

- Provision/bootstrap preflight catches unresolved env placeholders before long-running operations.
- ComfyUI service is persistent (`systemd`) and restarts automatically.
- Generation can be resumed by scenario and seed.
- Training output adapters are versioned by run ID.
- Model acquisition lockfile prevents silent model drift.
- Spaces archival has `--dry-run` and explicit opt-in via `ENABLE_SPACES_ARCHIVAL=true`.

