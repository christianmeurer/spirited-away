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


def main() -> int:
    parser = argparse.ArgumentParser(description="Run full INTERNAL_RND pipeline")
    parser.add_argument("--env-file", default="configs/env/digitalocean_h100.env")
    parser.add_argument("--dataset-input", default="Fotos-Aurora")
    parser.add_argument("--scenario", default="all", choices=["all", "scenario_a", "scenario_b", "scenario_c"])
    parser.add_argument("--skip-models", action="store_true")
    parser.add_argument("--skip-character-acquisition", action="store_true")
    parser.add_argument("--skip-dataset-prepare", action="store_true")
    parser.add_argument("--skip-training", action="store_true")
    parser.add_argument("--skip-generation", action="store_true")
    parser.add_argument("--dry-run-training", action="store_true")
    args = parser.parse_args()

    load_env_file(Path(args.env_file))

    python_exec = sys.executable

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
        run_cmd(
            [
                python_exec,
                "scripts/assets/acquire_character_refs.py",
                "--source-manifest",
                os.getenv(
                    "CHARACTER_SOURCE_MANIFEST",
                    "configs/characters/spirited_away_sources.internal_rnd.json",
                ),
                "--output-dir",
                os.getenv("CHARACTER_ASSET_OUTPUT_DIR", "data/character_refs"),
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
        run_cmd(
            [
                python_exec,
                "scripts/pipeline/run_scenarios.py",
                "--env-file",
                args.env_file,
                "--scenario",
                args.scenario,
            ]
        )

    print("Full pipeline execution finished.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

