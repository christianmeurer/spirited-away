#!/usr/bin/env python3
"""Internal R&D validator CLI for Aurora Fotos.

This tool validates scenario/track compatibility, structural fields, and quality gates.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_SCENARIO_CONFIG = Path("configs/scenarios/internal_rnd_scenarios.json")


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str]
    warnings: list[str]
    computed_metrics: dict[str, float]


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _has_value(obj: dict[str, Any], field: str) -> bool:
    return field in obj and obj[field] is not None


def _get_batch_items(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    batch = manifest.get("batch")
    if isinstance(batch, list):
        return [item for item in batch if isinstance(item, dict)]
    if isinstance(batch, dict):
        items = batch.get("items", [])
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


def _extract_bool_check(item: dict[str, Any], check_key: str) -> bool:
    checks = item.get("scenario_checks", {})
    if isinstance(checks, dict):
        return checks.get(check_key) is True
    if isinstance(checks, list):
        return check_key in checks
    return False


def _count_present_required_fields(
    manifest: dict[str, Any],
    items: list[dict[str, Any]],
    required_manifest_fields: list[str],
    required_item_fields: list[str],
) -> tuple[int, int]:
    present_manifest = sum(1 for field in required_manifest_fields if _has_value(manifest, field))
    total_manifest = len(required_manifest_fields)

    present_items = 0
    total_items = len(required_item_fields) * len(items)
    for item in items:
        present_items += sum(1 for field in required_item_fields if _has_value(item, field))

    return present_manifest + present_items, total_manifest + total_items


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _has_pending_human_scoring(items: list[dict[str, Any]]) -> bool:
    """Return True if any batch item has needs_human_scoring=True in its metrics."""
    for item in items:
        metrics = item.get("metrics", {})
        if isinstance(metrics, dict) and metrics.get("needs_human_scoring") is True:
            return True
    return False


def _compute_batch_metrics(
    manifest: dict[str, Any],
    items: list[dict[str, Any]],
    required_manifest_fields: list[str],
    required_item_fields: list[str],
) -> dict[str, float]:
    anatomy_fail_count = 0
    identity_scores: list[float] = []
    style_scores: list[float] = []
    pairing_scores: list[float] = []

    for item in items:
        metrics = item.get("metrics", {}) if isinstance(item.get("metrics"), dict) else {}

        if metrics.get("anatomy_failed") is True or metrics.get("anatomy_fail") is True:
            anatomy_fail_count += 1

        identity = metrics.get("identity_similarity")
        if isinstance(identity, (int, float)):
            identity_scores.append(float(identity))

        style = metrics.get("style_fidelity")
        if isinstance(style, (int, float)):
            style_scores.append(float(style))

        pairing = metrics.get("pairing_score")
        if isinstance(pairing, (int, float)):
            pairing_scores.append(float(pairing))

    batch_size = max(len(items), 1)
    present_required_fields, total_required_fields = _count_present_required_fields(
        manifest,
        items,
        required_manifest_fields,
        required_item_fields,
    )

    return {
        "anatomy_failure_rate": anatomy_fail_count / batch_size,
        "identity_mean": _mean(identity_scores),
        "identity_min": min(identity_scores) if identity_scores else 0.0,
        "style_fidelity": _mean(style_scores),
        "pairing_score": _mean(pairing_scores),
        "metadata_completeness": (
            present_required_fields / total_required_fields if total_required_fields else 0.0
        ),
    }


def _resolve_track_config(manifest: dict[str, Any], explicit_path: str | None) -> Path:
    if explicit_path:
        return Path(explicit_path)
    track_id = manifest.get("track_id")
    if not isinstance(track_id, str) or not track_id:
        raise ValueError("manifest.track_id is required to auto-resolve track config")
    return Path(f"configs/tracks/{track_id}.internal_rnd.json")


def validate_scenario_policy(
    manifest: dict[str, Any],
    scenarios_config: dict[str, Any],
    items: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    """Validate scenario declaration and scenario-specific checks."""
    errors: list[str] = []
    warnings: list[str] = []

    scenario_id = manifest.get("scenario_id")
    track_id = manifest.get("track_id")
    scenario_registry = scenarios_config.get("scenarios", {})

    if not isinstance(scenario_id, str) or scenario_id not in scenario_registry:
        errors.append("scenario_id is missing or not registered in scenario config")
        return errors, warnings

    scenario_rule = scenario_registry[scenario_id]

    if "track_id" in scenario_rule and scenario_rule["track_id"] != track_id:
        errors.append(
            f"scenario '{scenario_id}' requires track_id='{scenario_rule['track_id']}', got '{track_id}'"
        )

    if "allowed_track_ids" in scenario_rule:
        allowed = scenario_rule.get("allowed_track_ids", [])
        if track_id not in allowed:
            errors.append(
                f"scenario '{scenario_id}' does not allow track_id='{track_id}' (allowed: {allowed})"
            )

    for field in scenario_rule.get("required_manifest_fields", []):
        if not _has_value(manifest, field):
            errors.append(f"scenario '{scenario_id}' requires manifest field '{field}'")

    if scenario_rule.get("require_mixed_media_boundary_approval") is True:
        mmba = manifest.get("mixed_media_boundary_approval", {})
        if not (isinstance(mmba, dict) and mmba.get("approved") is True):
            errors.append(
                "scenario requires mixed_media_boundary_approval.approved=true"
            )

    required_checks = scenario_rule.get("required_item_checks", [])
    for idx, item in enumerate(items):
        for check in required_checks:
            if not _extract_bool_check(item, check):
                errors.append(f"batch.items[{idx}] missing scenario check '{check}'")

    if not items:
        warnings.append("manifest contains no batch items")

    return errors, warnings


def _validate_universal_gates(manifest: dict[str, Any], track_config: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    gates = manifest.get("gates", {}) if isinstance(manifest.get("gates"), dict) else {}

    for gate in track_config.get("required_universal_gates", []):
        if gate == "manual_final_candidate_approval":
            if manifest.get("manual_final_candidate_approval") is not True:
                errors.append("manual_final_candidate_approval must be true")
            continue
        if gates.get(gate) is not True:
            errors.append(f"universal gate '{gate}' must be true")
    return errors


def _validate_required_fields(
    manifest: dict[str, Any],
    items: list[dict[str, Any]],
    required_manifest_fields: list[str],
    required_item_fields: list[str],
) -> list[str]:
    errors: list[str] = []

    for field in required_manifest_fields:
        if not _has_value(manifest, field):
            errors.append(f"missing required manifest field '{field}'")

    for idx, item in enumerate(items):
        for field in required_item_fields:
            if not _has_value(item, field):
                errors.append(f"batch.items[{idx}] missing required field '{field}'")
    return errors


def _validate_thresholds(metrics: dict[str, float], track_config: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    thresholds = track_config.get("thresholds", {})

    max_anatomy = thresholds.get("anatomy_failure_rate_max")
    if isinstance(max_anatomy, (int, float)) and metrics["anatomy_failure_rate"] > max_anatomy:
        errors.append(
            f"anatomy_failure_rate exceeded: {metrics['anatomy_failure_rate']:.4f} > {max_anatomy}"
        )

    min_identity_mean = thresholds.get("identity_mean_min")
    if isinstance(min_identity_mean, (int, float)) and metrics["identity_mean"] < min_identity_mean:
        errors.append(
            f"identity_mean below threshold: {metrics['identity_mean']:.4f} < {min_identity_mean}"
        )

    min_identity_min = thresholds.get("identity_min_min")
    if isinstance(min_identity_min, (int, float)) and metrics["identity_min"] < min_identity_min:
        errors.append(
            f"identity_min below threshold: {metrics['identity_min']:.4f} < {min_identity_min}"
        )

    min_style = thresholds.get("style_fidelity_min")
    if isinstance(min_style, (int, float)) and metrics["style_fidelity"] < min_style:
        errors.append(
            f"style_fidelity below threshold: {metrics['style_fidelity']:.4f} < {min_style}"
        )

    min_pairing = thresholds.get("pairing_score_min")
    if isinstance(min_pairing, (int, float)) and metrics["pairing_score"] < min_pairing:
        errors.append(
            f"pairing_score below threshold: {metrics['pairing_score']:.4f} < {min_pairing}"
        )

    exact_metadata = thresholds.get("metadata_completeness_exact")
    if isinstance(exact_metadata, (int, float)) and abs(metrics["metadata_completeness"] - exact_metadata) > 1e-12:
        errors.append(
            "metadata_completeness must be exactly "
            f"{exact_metadata}, got {metrics['metadata_completeness']:.6f}"
        )

    return errors


def pre_export_guard(manifest: dict[str, Any]) -> tuple[bool, list[str]]:
    errors: list[str] = []

    outputs = manifest.get("outputs", [])
    if not isinstance(outputs, list):
        errors.append("pre-export guard: outputs must be a list")
        return False, errors

    for idx, output in enumerate(outputs):
        if not isinstance(output, dict):
            errors.append(f"pre-export guard: outputs[{idx}] must be an object")
            continue

        destination = output.get("destination")
        if not isinstance(destination, str) or not destination.strip():
            errors.append(f"pre-export guard: outputs[{idx}] destination must be a non-empty string")

    return len(errors) == 0, errors


def validate_manifest(
    manifest_path: Path,
    track_config_path: Path | None,
    scenario_config_path: Path,
) -> ValidationResult:
    manifest = _load_json(manifest_path)
    track_cfg = _load_json(
        _resolve_track_config(manifest, str(track_config_path) if track_config_path else None)
    )
    scenarios_cfg = _load_json(scenario_config_path)

    errors: list[str] = []
    warnings: list[str] = []

    items = _get_batch_items(manifest)

    # Emit early warning when metrics are still pending human scoring.
    # Threshold validation will produce meaningless failures (all zeros) until
    # human reviewers fill in actual scores. Warn rather than silently mislead.
    if _has_pending_human_scoring(items):
        warnings.append(
            "One or more batch items have needs_human_scoring=true. "
            "Metric thresholds cannot be meaningfully evaluated until human reviewers "
            "fill in actual scores (identity_similarity, style_fidelity, pairing_score). "
            "Run strict validation only after scoring is complete."
        )

    required_manifest_fields = track_cfg.get("required_manifest_fields", [])
    required_item_fields = track_cfg.get("required_batch_item_fields", [])

    errors.extend(_validate_required_fields(manifest, items, required_manifest_fields, required_item_fields))
    errors.extend(_validate_universal_gates(manifest, track_cfg))

    scenario_errors, scenario_warnings = validate_scenario_policy(manifest, scenarios_cfg, items)
    errors.extend(scenario_errors)
    warnings.extend(scenario_warnings)

    computed_metrics = _compute_batch_metrics(
        manifest,
        items,
        required_manifest_fields,
        required_item_fields,
    )
    errors.extend(_validate_thresholds(computed_metrics, track_cfg))

    pre_export_ok, pre_export_errors = pre_export_guard(manifest)
    if not pre_export_ok:
        errors.extend(pre_export_errors)

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        computed_metrics=computed_metrics,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Aurora Fotos INTERNAL_RND validation CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="Validate manifest against scenario/config gates")
    validate_parser.add_argument("--manifest", required=True, help="Path to manifest JSON")
    validate_parser.add_argument("--track-config", required=False, help="Optional explicit track config path")
    validate_parser.add_argument(
        "--scenario-config",
        required=False,
        default=str(DEFAULT_SCENARIO_CONFIG),
        help="Scenario config path",
    )

    guard_parser = subparsers.add_parser("pre-export-guard", help="Run structural pre-export guard checks")
    guard_parser.add_argument("--manifest", required=True, help="Path to manifest JSON")

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "pre-export-guard":
        manifest = _load_json(Path(args.manifest))
        ok, errors = pre_export_guard(manifest)
        payload = {
            "valid": ok,
            "errors": errors,
        }
        print(json.dumps(payload, indent=2))
        return 0 if ok else 1

    result = validate_manifest(
        manifest_path=Path(args.manifest),
        track_config_path=Path(args.track_config) if args.track_config else None,
        scenario_config_path=Path(args.scenario_config),
    )

    payload = {
        "valid": result.valid,
        "errors": result.errors,
        "warnings": result.warnings,
        "computed_metrics": result.computed_metrics,
    }
    print(json.dumps(payload, indent=2))
    return 0 if result.valid else 1


if __name__ == "__main__":
    sys.exit(main())
