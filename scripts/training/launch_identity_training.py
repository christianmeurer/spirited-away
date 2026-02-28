#!/usr/bin/env python3
"""Launch identity adapter training via external trainer command template."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
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


def parse_boolish(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean-like value: {value}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch identity adapter training")
    parser.add_argument("--env-file", default="configs/env/digitalocean_h100.env")
    parser.add_argument("--dataset-dir", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--trigger-token", default=None)
    parser.add_argument("--steps", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--learning-rate", type=float, default=None)
    parser.add_argument("--rank", type=int, default=None)
    parser.add_argument("--enable-dop", default=None)
    parser.add_argument("--timestep-bias", default=None)
    parser.add_argument("--resolution", default=None)
    parser.add_argument(
        "--trainer-cmd",
        default=None,
        help=(
            "Command template with placeholders: {dataset_dir}, {output_dir}, {trigger_token}, "
            "{steps}, {batch_size}, {learning_rate}, {rank}, {enable_dop}, {timestep_bias}, {resolution}"
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_env_file(Path(args.env_file))

    dataset_dir = args.dataset_dir or os.getenv("IDENTITY_DATASET_DIR")
    base_output_dir = Path(args.output_dir or os.getenv("IDENTITY_OUTPUT_DIR", "models/identity_adapters"))
    trigger_token = args.trigger_token or os.getenv("IDENTITY_TRIGGER_TOKEN")
    steps = args.steps or int(os.getenv("TRAIN_STEPS", "2500"))
    batch_size = args.batch_size or int(os.getenv("TRAIN_BATCH_SIZE", "2"))
    learning_rate = args.learning_rate or float(os.getenv("TRAIN_LEARNING_RATE", "0.0001"))
    rank = args.rank or int(os.getenv("TRAIN_ADAPTER_RANK", "32"))
    enable_dop_raw = args.enable_dop if args.enable_dop is not None else os.getenv("TRAIN_ENABLE_DOP", "true")
    enable_dop = parse_boolish(enable_dop_raw)
    timestep_bias = args.timestep_bias or os.getenv("TRAIN_TIMESTEP_BIAS", "balanced")
    resolution = args.resolution or os.getenv("TRAIN_RESOLUTION", "1024x1024,1408x1408")

    if not dataset_dir:
        raise SystemExit("dataset_dir is required (arg or IDENTITY_DATASET_DIR env)")
    if not trigger_token:
        raise SystemExit("trigger_token is required (arg or IDENTITY_TRIGGER_TOKEN env)")

    run_id = datetime.now(timezone.utc).strftime("identity_train_%Y%m%dT%H%M%SZ")
    output_dir = base_output_dir / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    trainer_cmd = args.trainer_cmd or os.getenv("TRAINER_COMMAND_TEMPLATE")
    if not trainer_cmd:
        raise SystemExit(
            "TRAINER_COMMAND_TEMPLATE not set. Provide --trainer-cmd. "
            "Example: python -m ostris_ai_toolkit.train --dataset {dataset_dir} "
            "--output {output_dir} --trigger {trigger_token} --steps {steps} "
            "--batch-size {batch_size} --learning-rate {learning_rate} --rank {rank} "
            "--enable-dop {enable_dop} --timestep-bias {timestep_bias} --resolution {resolution}"
        )

    command = trainer_cmd.format(
        dataset_dir=dataset_dir,
        output_dir=str(output_dir),
        trigger_token=trigger_token,
        steps=steps,
        batch_size=batch_size,
        learning_rate=learning_rate,
        rank=rank,
        enable_dop=str(enable_dop).lower(),
        timestep_bias=timestep_bias,
        resolution=resolution,
    )

    launch_manifest = {
        "run_id": run_id,
        "dataset_dir": dataset_dir,
        "output_dir": str(output_dir),
        "trigger_token": trigger_token,
        "steps": steps,
        "batch_size": batch_size,
        "learning_rate": learning_rate,
        "rank": rank,
        "enable_dop": enable_dop,
        "timestep_bias": timestep_bias,
        "resolution": resolution,
        "trainer_command": command,
        "dry_run": args.dry_run,
    }
    Path("manifests").mkdir(parents=True, exist_ok=True)
    manifest_path = Path("manifests") / f"{run_id}.json"
    manifest_path.write_text(json.dumps(launch_manifest, indent=2), encoding="utf-8")

    if args.dry_run:
        print(json.dumps({"dry_run": True, "command": command, "manifest": str(manifest_path)}, indent=2))
        return 0

    print(f"Executing: {command}")
    completed = subprocess.run(command, shell=True, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())

