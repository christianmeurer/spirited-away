# Aurora Fotos INTERNAL_RND Pipeline Runbook

This runbook operationalizes the internal plan in [`plans/aurora-fotos-plan.md`](plans/aurora-fotos-plan.md).

## Scope

- Internal-only experimentation (`INTERNAL_RND`).
- Still-image outputs only.
- DigitalOcean-only execution policy for generation workloads.

## Artifacts

- Scenario policy: [`configs/scenarios/internal_rnd_scenarios.json`](configs/scenarios/internal_rnd_scenarios.json)
- Track A thresholds: [`configs/tracks/track_a.internal_rnd.json`](configs/tracks/track_a.internal_rnd.json)
- Track B thresholds: [`configs/tracks/track_b.internal_rnd.json`](configs/tracks/track_b.internal_rnd.json)
- Track C thresholds: [`configs/tracks/track_c.internal_rnd.json`](configs/tracks/track_c.internal_rnd.json)
- Character source config: [`configs/characters/spirited_away_sources.internal_rnd.json`](configs/characters/spirited_away_sources.internal_rnd.json)
- Validator CLI: [`scripts/internal_rnd_cli.py`](scripts/internal_rnd_cli.py)
- Character acquisition: [`scripts/assets/acquire_character_refs.py`](scripts/assets/acquire_character_refs.py)
- Character source generation: [`scripts/assets/generate_character_source_images.py`](scripts/assets/generate_character_source_images.py)
- Quality-ranked rename: [`scripts/assets/rename_quality_ranked_dataset.py`](scripts/assets/rename_quality_ranked_dataset.py)
- Generation + rename workflow runner: [`scripts/assets/run_character_asset_generation_and_rename.py`](scripts/assets/run_character_asset_generation_and_rename.py)
- Quality analysis: [`scripts/assets/analyze_dataset_quality.py`](scripts/assets/analyze_dataset_quality.py)
- Full pipeline entrypoint: [`scripts/pipeline/run_full_pipeline.py`](scripts/pipeline/run_full_pipeline.py)
- Spaces archival utility: [`scripts/pipeline/archive_to_spaces.py`](scripts/pipeline/archive_to_spaces.py)
- Example manifests:
  - [`manifests/examples/track_a_batch_example.internal_rnd.json`](manifests/examples/track_a_batch_example.internal_rnd.json)
  - [`manifests/examples/track_b_batch_example.internal_rnd.json`](manifests/examples/track_b_batch_example.internal_rnd.json)

## Character References: Automated Acquisition + Quality Ranking

The character reference flow is now automated and idempotent:

- Reads configured entries from [`configs/characters/spirited_away_sources.internal_rnd.json`](configs/characters/spirited_away_sources.internal_rnd.json).
- Supports `local_folder`, `local_file`/`manual_upload`, and `url`/`licensed_url` source kinds.
- Deduplicates assets by SHA-256 across reruns.
- Scores references with deterministic objective metrics.
- Normalizes filenames by usefulness rank:
  - `<character_id>__scoreXX__rankYY.ext`
- Persists deterministic state in:
  - `data/character_refs/.character_refs_state.json`
- Writes acquisition manifests:
  - `manifests/character_assets_acquisition.<timestamp>.json`
  - `manifests/character_assets_acquisition.latest.json`

No licensing/policy attestation gates block this internal acquisition flow.

### Source Staging Layout

Put source images into character folders under:

- `data/character_refs_sources/<character_id>/`

Character IDs are declared in [`configs/characters/spirited_away_sources.internal_rnd.json`](configs/characters/spirited_away_sources.internal_rnd.json), including aliases (for example Lin/Linha, No-Face/Kaonashi, Aogaeru/Frog).

### Deterministic Source Generation + Dataset Renaming Workflow

Single command workflow that:

1. Generates deterministic local source images for each `local_folder` asset.
2. Acquires + quality-ranks `data/character_refs`.
3. Applies quality-ranked deterministic renaming to both:
   - `data/Fotos-Aurora`
   - `data/character_refs`
4. Writes rename mapping artifacts under `manifests/`.

```bash
python scripts/assets/run_character_asset_generation_and_rename.py --source-manifest configs/characters/spirited_away_sources.internal_rnd.json --local-source-root data/character_refs_sources --character-output-dir data/character_refs --fotos-dir data/Fotos-Aurora --images-per-asset 6
```

Optional deterministic refresh of generated source files and stale cleanup:

```bash
python scripts/assets/run_character_asset_generation_and_rename.py --source-manifest configs/characters/spirited_away_sources.internal_rnd.json --local-source-root data/character_refs_sources --character-output-dir data/character_refs --fotos-dir data/Fotos-Aurora --images-per-asset 6 --refresh-generated-sources --purge-stale-generated-sources
```

### Acquisition Command

```bash
python scripts/assets/acquire_character_refs.py --source-manifest configs/characters/spirited_away_sources.internal_rnd.json --output-dir data/character_refs --local-source-root data/character_refs_sources --quality-report manifests/image_quality_report.json
```

Optional strictness to fail CI when configured assets are missing local sources:

```bash
python scripts/assets/acquire_character_refs.py --source-manifest configs/characters/spirited_away_sources.internal_rnd.json --output-dir data/character_refs --local-source-root data/character_refs_sources --quality-report manifests/image_quality_report.json --fail-on-missing-assets
```

### Independent Quality Audit Command

```bash
python scripts/assets/analyze_dataset_quality.py --input-dir data/character_refs --output manifests/image_quality_report.audit.json
```

## Phase Execution

1. Train and promote a track-scoped identity adapter.
2. Produce translation assets for Scenario A and Scenario B.
3. Build composition batches with deterministic seeds.
4. Run generation first (operational mode), then run strict validation as an explicit step.
5. Complete human review and manual final-candidate approval.
6. Run pre-export guard and archive internal artifacts.

## End-to-End Full Pipeline Command

Default full run (includes character acquisition + quality audit):

```bash
python scripts/pipeline/run_full_pipeline.py --env-file configs/env/digitalocean_h100.env
```

Scenario C contract values (from env or CLI):

- `SCENARIO_C_TRACK`
- `SCENARIO_C_SUBJECT_IMAGE`
- `SCENARIO_C_COMPANION_IMAGE_A`
- `SCENARIO_C_COMPANION_IMAGE_B`
- `SCENARIO_C_SUBJECT_MASK_IMAGE`
- `SCENARIO_C_COMPANION_MASK_IMAGE`
- `SCENARIO_C_PS_BLEND_MODE`

Default behavior for [`scripts/pipeline/run_full_pipeline.py`](scripts/pipeline/run_full_pipeline.py):

- `--scenario all`: Scenario A/B proceed normally.
- Scenario C is auto-skipped with warnings if Scenario C contract values are missing or referenced assets do not exist.
- Use `--require-scenario-c` to enforce strict fail-fast behavior for `--scenario all`.

Strict behavior is always preserved for explicit Scenario C runs (`--scenario scenario_c`): missing Scenario C contract values/assets fail the run.

Scenario C focused run with explicit overrides:

```bash
python scripts/pipeline/run_full_pipeline.py \
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

Strict all-scenarios run (require Scenario C contract and assets):

```bash
python scripts/pipeline/run_full_pipeline.py --env-file configs/env/digitalocean_h100.env --scenario all --require-scenario-c
```

Common selective run:

```bash
python scripts/pipeline/run_full_pipeline.py --env-file configs/env/digitalocean_h100.env --skip-models --skip-training --skip-generation --character-fail-on-missing-assets
```

Useful flags on [`scripts/pipeline/run_full_pipeline.py`](scripts/pipeline/run_full_pipeline.py):

- `--character-source-manifest`
- `--character-output-dir`
- `--character-source-root`
- `--character-quality-report`
- `--character-quality-audit-report`
- `--character-request-timeout`
- `--character-fail-on-missing-assets`
- `--skip-character-acquisition`
- `--skip-character-quality-audit`
- `--scenario-c-track`
- `--scenario-c-subject-image`
- `--scenario-c-companion-image-a`
- `--scenario-c-companion-image-b`
- `--scenario-c-subject-mask-image`
- `--scenario-c-companion-mask-image`
- `--scenario-c-ps-blend-mode`
- `--require-scenario-c`

## Scenario Generation Entry Point

[`scripts/pipeline/run_scenarios.py`](scripts/pipeline/run_scenarios.py) supports Scenario C contract flags directly and validates Scenario C track policy against [`configs/scenarios/internal_rnd_scenarios.json`](configs/scenarios/internal_rnd_scenarios.json).

Operational defaults:

- `--scenario all`: Scenario C auto-skips with warnings when Scenario C contract or asset preflight is incomplete.
- `--require-scenario-c`: strict opt-in to fail instead of skipping Scenario C in `all` mode.
- Generated manifests are explicitly marked pending approvals/metrics; strict compliance validation is a separate explicit command.

Example:

```bash
python scripts/pipeline/run_scenarios.py \
  --env-file configs/env/digitalocean_h100.env \
  --scenario scenario_c \
  --scenario-c-track track_c
```

## Validation Commands

Use Python to validate a full manifest:

```bash
python scripts/internal_rnd_cli.py validate --manifest manifests/examples/track_a_batch_example.internal_rnd.json
python scripts/internal_rnd_cli.py validate --manifest manifests/examples/track_b_batch_example.internal_rnd.json
```

For Scenario C outputs, validation resolves [`configs/tracks/track_c.internal_rnd.json`](configs/tracks/track_c.internal_rnd.json) from manifest `track_id=track_c`.

Run only pre-export guard checks:

```bash
python scripts/internal_rnd_cli.py pre-export-guard --manifest manifests/examples/track_a_batch_example.internal_rnd.json
```

## Mandatory Gates (Enforced)

- Scenario declaration and track compatibility.
- Required per-item `scenario_checks` for each scenario.
- Deterministic metrics against configured thresholds.
- Universal gates:
  - `human_review_approved`
  - `manual_final_candidate_approval`
  - `pre_export_guard_passed`
  - `track_isolation_passed`
- Metadata completeness must resolve to `1.0`.

## Internal Archival to DigitalOcean Spaces

Runbook-promised archival is implemented in [`scripts/pipeline/archive_to_spaces.py`](scripts/pipeline/archive_to_spaces.py). It is conservative and internal-use oriented:

- Requires explicit opt-in: `ENABLE_SPACES_ARCHIVAL=true`
- Requires Spaces credentials and bucket/endpoint env keys
- Supports `--dry-run` for non-mutating verification

Archive generated manifests:

```bash
python scripts/pipeline/archive_to_spaces.py \
  --env-file configs/env/digitalocean_h100.env \
  --input manifests/generated
```

Dry-run preview:

```bash
python scripts/pipeline/archive_to_spaces.py \
  --env-file configs/env/digitalocean_h100.env \
  --input manifests/generated \
  --dry-run
```

