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
- Validator CLI: [`scripts/internal_rnd_cli.py`](scripts/internal_rnd_cli.py)
- Example manifests:
  - [`manifests/examples/track_a_batch_example.internal_rnd.json`](manifests/examples/track_a_batch_example.internal_rnd.json)
  - [`manifests/examples/track_b_batch_example.internal_rnd.json`](manifests/examples/track_b_batch_example.internal_rnd.json)

## Phase Execution

1. Train and promote a track-scoped identity adapter.
2. Produce translation assets for Scenario A and Scenario B.
3. Build composition batches with deterministic seeds.
4. Run automated policy + threshold validation.
5. Complete human review and manual final-candidate approval.
6. Run pre-export guard and archive internal artifacts.

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

## Provider Policy

Accepted execution order:

1. `digitalocean_primary_region`
2. `digitalocean_secondary_region`
3. `local_fallback_for_pre_post_only`

Any multi-cloud fallback is prohibited.

