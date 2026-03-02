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
- Character source config: [`configs/characters/spirited_away_sources.internal_rnd.json`](configs/characters/spirited_away_sources.internal_rnd.json)
- Validator CLI: [`scripts/internal_rnd_cli.py`](scripts/internal_rnd_cli.py)
- Character acquisition: [`scripts/assets/acquire_character_refs.py`](scripts/assets/acquire_character_refs.py)
- Character source generation: [`scripts/assets/generate_character_source_images.py`](scripts/assets/generate_character_source_images.py)
- Quality-ranked rename: [`scripts/assets/rename_quality_ranked_dataset.py`](scripts/assets/rename_quality_ranked_dataset.py)
- Generation + rename workflow runner: [`scripts/assets/run_character_asset_generation_and_rename.py`](scripts/assets/run_character_asset_generation_and_rename.py)
- Quality analysis: [`scripts/assets/analyze_dataset_quality.py`](scripts/assets/analyze_dataset_quality.py)
- Full pipeline entrypoint: [`scripts/pipeline/run_full_pipeline.py`](scripts/pipeline/run_full_pipeline.py)
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
4. Run automated structural + threshold validation.
5. Complete human review and manual final-candidate approval.
6. Run pre-export guard and archive internal artifacts.

## End-to-End Full Pipeline Command

Default full run (includes character acquisition + quality audit):

```bash
python scripts/pipeline/run_full_pipeline.py --env-file configs/env/digitalocean_h100.env
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

## Validation Commands

Use Python to validate a full manifest:

```bash
python scripts/internal_rnd_cli.py validate --manifest manifests/examples/track_a_batch_example.internal_rnd.json
python scripts/internal_rnd_cli.py validate --manifest manifests/examples/track_b_batch_example.internal_rnd.json
```

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

