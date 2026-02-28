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

1. Provision droplet and bootstrap baseline dependencies.
2. Pull or update GitHub repository on VM.
3. Sync env file and secret values (`HF_TOKEN`, Spaces credentials, SSH keys).
4. Acquire models from Hugging Face registry.
5. Acquire and validate character references from approved sources.
6. Prepare identity dataset and run adapter training.
7. Execute scenario generation pipeline (A/B/C).
8. Validate resulting manifests and quality gates.
9. Run pre-export guard and archive to internal Spaces paths.

## Implementation Commands

Control machine provisioning:

```bash
cp configs/env/digitalocean_h100.env.example configs/env/digitalocean_h100.env
./scripts/deploy/provision_do_h100.sh configs/env/digitalocean_h100.env
```

On VM bootstrap and repo setup:

```bash
cd /opt/aurora/Aurora-Fotos
./scripts/deploy/bootstrap_vm.sh configs/env/digitalocean_h100.env
./scripts/deploy/update_repo.sh configs/env/digitalocean_h100.env
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

Run all scenarios (A/B/C) through ComfyUI:

```bash
python scripts/pipeline/run_scenarios.py \
  --env-file configs/env/digitalocean_h100.env \
  --scenario all
```

Orchestrate full flow in one command:

```bash
python scripts/pipeline/run_full_pipeline.py \
  --env-file configs/env/digitalocean_h100.env \
  --scenario all \
  --dry-run-training
```

Validate generated manifests:

```bash
python scripts/internal_rnd_cli.py validate --manifest manifests/generated/<run_id>_scenario_a.internal_rnd.json
python scripts/internal_rnd_cli.py validate --manifest manifests/generated/<run_id>_scenario_b.internal_rnd.json
python scripts/internal_rnd_cli.py validate --manifest manifests/generated/<run_id>_scenario_c.internal_rnd.json
```

## Failure Domains and Recovery

- All long jobs produce manifests and checkpoints for resumability.
- Generation can be resumed by scenario and seed.
- Training output adapters are versioned by run ID.
- Model acquisition lockfile prevents silent model drift.

