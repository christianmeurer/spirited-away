#!/usr/bin/env python3
"""Orchestrate full Aurora INTERNAL_RND pipeline for scenarios A/B/C."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def run_cmd(cmd: list[str]) -> None:
    print("Executing:", " ".join(cmd))
    completed = subprocess.run(cmd, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"Command failed ({completed.returncode}): {' '.join(cmd)}")


def _resolve_arg_or_env(arg_value: str | None, env_key: str) -> str:
    if arg_value is not None and arg_value.strip():
        return arg_value.strip()
    return os.getenv(env_key, "").strip()


def _resolve_comfy_asset_path(comfy_root: Path, asset_ref: str) -> Path:
    candidate = Path(asset_ref)
    if candidate.is_absolute():
        return candidate
    first = comfy_root / candidate
    if first.exists():
        return first
    return comfy_root / "input" / candidate


def main() -> int:
    parser = argparse.ArgumentParser(description="Run full INTERNAL_RND pipeline")
    parser.add_argument("--env-file", default="configs/env/digitalocean_h100.env")
    parser.add_argument("--dataset-input", default="Fotos-Aurora")
    parser.add_argument("--scenario", default="all", choices=["all", "scenario_a", "scenario_b", "scenario_c"])
    parser.add_argument("--character-source-manifest", default=None)
    parser.add_argument("--character-output-dir", default=None)
    parser.add_argument("--character-source-root", default=None)
    parser.add_argument("--character-quality-report", default=None)
    parser.add_argument("--character-quality-audit-report", default=None)
    parser.add_argument("--character-request-timeout", type=int, default=120)
    parser.add_argument("--character-fail-on-missing-assets", action="store_true")
    parser.add_argument("--skip-models", action="store_true")
    parser.add_argument("--skip-character-acquisition", action="store_true")
    parser.add_argument("--skip-character-quality-audit", action="store_true")
    parser.add_argument("--skip-dataset-prepare", action="store_true")
    parser.add_argument("--skip-training", action="store_true")
    parser.add_argument("--skip-generation", action="store_true")
    parser.add_argument("--dry-run-training", action="store_true")
    parser.add_argument("--scenario-c-track", default=None)
    parser.add_argument("--scenario-c-subject-image", default=None)
    parser.add_argument("--scenario-c-companion-image-a", default=None)
    parser.add_argument("--scenario-c-companion-image-b", default=None)
    parser.add_argument("--scenario-c-subject-mask-image", default=None)
    parser.add_argument("--scenario-c-companion-mask-image", default=None)
    parser.add_argument("--scenario-c-ps-blend-mode", default=None)
    parser.add_argument(
        "--require-scenario-c",
        action="store_true",
        help="When --scenario all is used, fail if Scenario C contract is missing instead of auto-skipping Scenario C.",
    )
    args = parser.parse_args()

    load_env_file(Path(args.env_file))

    python_exec = sys.executable
    character_source_manifest = args.character_source_manifest or os.getenv(
        "CHARACTER_SOURCE_MANIFEST",
        "configs/characters/spirited_away_sources.internal_rnd.json",
    )
    character_output_dir = args.character_output_dir or os.getenv("CHARACTER_ASSET_OUTPUT_DIR", "data/character_refs")
    character_source_root = args.character_source_root or os.getenv(
        "CHARACTER_SOURCE_ROOT",
        "data/character_refs_sources",
    )
    character_quality_report = args.character_quality_report or os.getenv(
        "CHARACTER_QUALITY_REPORT",
        "manifests/image_quality_report.json",
    )
    character_quality_audit_report = args.character_quality_audit_report or os.getenv(
        "CHARACTER_QUALITY_AUDIT_REPORT",
        "manifests/image_quality_report.audit.json",
    )
    comfy_root = Path(os.getenv("COMFYUI_ROOT", "/opt/aurora/ComfyUI"))

    scenario_c_requested = args.scenario in {"all", "scenario_c"}
    scenario_c_assets = {
        "scenario-c-subject-image": _resolve_arg_or_env(
            args.scenario_c_subject_image,
            "SCENARIO_C_SUBJECT_IMAGE",
        ),
        "scenario-c-companion-image-a": _resolve_arg_or_env(
            args.scenario_c_companion_image_a,
            "SCENARIO_C_COMPANION_IMAGE_A",
        ),
        "scenario-c-companion-image-b": _resolve_arg_or_env(
            args.scenario_c_companion_image_b,
            "SCENARIO_C_COMPANION_IMAGE_B",
        ),
        "scenario-c-subject-mask-image": _resolve_arg_or_env(
            args.scenario_c_subject_mask_image,
            "SCENARIO_C_SUBJECT_MASK_IMAGE",
        ),
        "scenario-c-companion-mask-image": _resolve_arg_or_env(
            args.scenario_c_companion_mask_image,
            "SCENARIO_C_COMPANION_MASK_IMAGE",
        ),
    }
    scenario_c_track = _resolve_arg_or_env(args.scenario_c_track, "SCENARIO_C_TRACK")
    scenario_c_ps_blend_mode = _resolve_arg_or_env(
        args.scenario_c_ps_blend_mode,
        "SCENARIO_C_PS_BLEND_MODE",
    )

    if scenario_c_requested and not args.skip_generation:
        missing = [name for name, value in scenario_c_assets.items() if not value]
        if not scenario_c_track:
            missing.append("scenario-c-track")
        if not scenario_c_ps_blend_mode:
            missing.append("scenario-c-ps-blend-mode")

        missing_files: list[str] = []
        if not missing:
            for name, asset_ref in scenario_c_assets.items():
                resolved = _resolve_comfy_asset_path(comfy_root, asset_ref)
                if not resolved.exists():
                    missing_files.append(f"{name}={asset_ref} (expected at {resolved})")

        if missing or missing_files:
            reason_lines: list[str] = []
            if missing:
                reason_lines.append("missing values: " + ", ".join(sorted(missing)))
            if missing_files:
                reason_lines.append("missing files:")
                reason_lines.extend(f"  - {entry}" for entry in missing_files)

            if args.scenario == "all" and not args.require_scenario_c:
                print("WARNING: Scenario C preflight failed in --scenario all. Scenario C will be auto-skipped.")
                for line in reason_lines:
                    print(f"WARNING: {line}")
            else:
                raise ValueError(
                    "Scenario C is required but preflight failed:\n" + "\n".join(reason_lines)
                )

    if not args.skip_models:
        run_cmd(
            [
                python_exec,
                "scripts/models/fetch_hf_models.py",
                "--env-file",
                args.env_file,
                "--allow-optional-failures",
            ]
        )

    if not args.skip_character_acquisition:
        acquire_cmd = [
            python_exec,
            "scripts/assets/acquire_character_refs.py",
            "--source-manifest",
            character_source_manifest,
            "--output-dir",
            character_output_dir,
            "--local-source-root",
            character_source_root,
            "--quality-report",
            character_quality_report,
            "--request-timeout",
            str(args.character_request_timeout),
        ]
        if args.character_fail_on_missing_assets:
            acquire_cmd.append("--fail-on-missing-assets")
        run_cmd(acquire_cmd)

    if not args.skip_character_quality_audit:
        run_cmd(
            [
                python_exec,
                "scripts/assets/analyze_dataset_quality.py",
                "--input-dir",
                character_output_dir,
                "--output",
                character_quality_audit_report,
            ]
        )

    if not args.skip_dataset_prepare:
        run_cmd(
            [
                python_exec,
                "scripts/training/prepare_identity_dataset.py",
                "--input-dir",
                args.dataset_input,
                "--output-dir",
                os.getenv("IDENTITY_DATASET_DIR", "data/identity_dataset"),
                "--trigger-token",
                os.getenv("IDENTITY_TRIGGER_TOKEN", "[subj_name_2026]"),
            ]
        )

    if not args.skip_training:
        train_cmd = [
            python_exec,
            "scripts/training/launch_identity_training.py",
            "--env-file",
            args.env_file,
        ]
        if args.dry_run_training:
            train_cmd.append("--dry-run")
        run_cmd(train_cmd)

    if not args.skip_generation:
        generation_cmd = [
            python_exec,
            "scripts/pipeline/run_scenarios.py",
            "--env-file",
            args.env_file,
            "--scenario",
            args.scenario,
        ]

        if scenario_c_requested:
            generation_cmd.extend(
                [
                    "--scenario-c-track",
                    scenario_c_track,
                    "--scenario-c-subject-image",
                    scenario_c_assets["scenario-c-subject-image"],
                    "--scenario-c-companion-image-a",
                    scenario_c_assets["scenario-c-companion-image-a"],
                    "--scenario-c-companion-image-b",
                    scenario_c_assets["scenario-c-companion-image-b"],
                    "--scenario-c-subject-mask-image",
                    scenario_c_assets["scenario-c-subject-mask-image"],
                    "--scenario-c-companion-mask-image",
                    scenario_c_assets["scenario-c-companion-mask-image"],
                    "--scenario-c-ps-blend-mode",
                    scenario_c_ps_blend_mode,
                ]
            )
        if args.require_scenario_c:
            generation_cmd.append("--require-scenario-c")

        run_cmd(generation_cmd)

    print("Full pipeline execution finished.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

