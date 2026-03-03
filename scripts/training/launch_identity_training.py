#!/usr/bin/env python3
"""Launch identity adapter training via Ostris AI Toolkit.

Workflow:
  1. Resolve training parameters from CLI args or env file.
  2. Generate an AI Toolkit YAML config via generate_training_config.py.
  3. Invoke: python {ai_toolkit_root}/run.py {config_yaml_path}

The AI Toolkit (https://github.com/ostris/ai-toolkit) must be cloned
separately and its path set in AI_TOOLKIT_ROOT (env or --ai-toolkit-root arg).

After training completes, copy the latest LoRA safetensors to
ComfyUI/models/loras/{trigger_token}_latest.safetensors so the workflow
template can reference it via IDENTITY_LORA_NAME.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
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


def parse_resolution_list(raw_resolution: str) -> list[int]:
    """Parse TRAIN_RESOLUTION values from env/CLI into integer edge sizes.

    Supported formats:
      - "1024,1408"
      - "1024x1024,1408x1408"
      - Mixed forms like "1024,1408x1408"
    """
    values: list[int] = []

    for token in raw_resolution.split(","):
        clean = token.strip().lower()
        if not clean:
            continue

        if "x" in clean:
            parts = [p.strip() for p in clean.split("x") if p.strip()]
            if len(parts) != 2:
                raise ValueError(
                    "Invalid TRAIN_RESOLUTION token "
                    f"'{token}'. Expected INT or WIDTHxHEIGHT."
                )
            try:
                width = int(parts[0])
                height = int(parts[1])
            except ValueError as exc:
                raise ValueError(
                    "Invalid TRAIN_RESOLUTION token "
                    f"'{token}'. Width/height must be integers."
                ) from exc
            values.extend([width, height])
            continue

        try:
            values.append(int(clean))
        except ValueError as exc:
            raise ValueError(
                "Invalid TRAIN_RESOLUTION token "
                f"'{token}'. Expected INT or WIDTHxHEIGHT."
            ) from exc

    # Keep first-seen order while deduplicating.
    deduped = list(dict.fromkeys(values))
    if not deduped:
        raise ValueError("No valid values found in TRAIN_RESOLUTION.")
    return deduped


def _find_latest_lora(output_dir: Path) -> Path | None:
    """Return the most recently modified .safetensors file under output_dir."""
    candidates = sorted(
        output_dir.rglob("*.safetensors"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Launch FLUX.2 identity adapter training via Ostris AI Toolkit"
    )
    parser.add_argument("--env-file", default="configs/env/digitalocean_h100.env")
    parser.add_argument(
        "--ai-toolkit-root",
        default=None,
        help="Path to cloned ostris/ai-toolkit repository. Defaults to AI_TOOLKIT_ROOT env.",
    )
    parser.add_argument(
        "--ai-toolkit-python",
        default=None,
        help=(
            "Python executable to run ai-toolkit (e.g. /opt/aurora/ai-toolkit/.venv/bin/python). "
            "Defaults to AI_TOOLKIT_PYTHON env or {ai_toolkit_root}/.venv/bin/python when present."
        ),
    )
    parser.add_argument("--dataset-dir", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--trigger-token", default=None)
    parser.add_argument("--caption-suffix", default="person")
    parser.add_argument("--steps", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--learning-rate", type=float, default=None)
    parser.add_argument("--rank", type=int, default=None)
    parser.add_argument("--weight-decay", type=float, default=None)
    parser.add_argument("--optimizer", default=None, help="Optimizer name (default: AdamW8Bit)")
    parser.add_argument("--enable-dop", default=None)
    parser.add_argument(
        "--quantize",
        default=None,
        help="Enable model quantization for training config (true/false). Defaults to TRAIN_QUANTIZE or false.",
    )
    parser.add_argument("--timestep-bias", default=None)
    parser.add_argument("--resolution", default=None, help="Comma-separated resolution list, e.g. 1024,1408")
    parser.add_argument(
        "--flux2-model-path",
        default=None,
        help=(
            "Path to FLUX.2-dev model directory or HuggingFace repo ID. "
            "Defaults to FLUX2_MODEL_PATH env or the local snapshot path."
        ),
    )
    parser.add_argument(
        "--comfyui-loras-dir",
        default=None,
        help="If set, copy the trained LoRA to this directory as {trigger_token}_latest.safetensors",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_env_file(Path(args.env_file))

    ai_toolkit_root = Path(
        args.ai_toolkit_root or os.getenv("AI_TOOLKIT_ROOT", "/opt/aurora/ai-toolkit")
    )
    ai_toolkit_python_arg = args.ai_toolkit_python or os.getenv("AI_TOOLKIT_PYTHON", "")
    dataset_dir = args.dataset_dir or os.getenv("IDENTITY_DATASET_DIR")
    base_output_dir = Path(
        args.output_dir or os.getenv("IDENTITY_OUTPUT_DIR", "models/identity_adapters")
    )
    trigger_token = args.trigger_token or os.getenv("IDENTITY_TRIGGER_TOKEN")
    caption_suffix = args.caption_suffix
    steps = args.steps or int(os.getenv("TRAIN_STEPS", "2500"))
    batch_size = args.batch_size or int(os.getenv("TRAIN_BATCH_SIZE", "2"))
    learning_rate = args.learning_rate or float(os.getenv("TRAIN_LEARNING_RATE", "0.0001"))
    rank = args.rank or int(os.getenv("TRAIN_ADAPTER_RANK", "32"))
    weight_decay = args.weight_decay or float(os.getenv("TRAIN_WEIGHT_DECAY", "0.0001"))
    optimizer = args.optimizer or os.getenv("TRAIN_OPTIMIZER", "AdamW8Bit")
    enable_dop_raw = (
        args.enable_dop if args.enable_dop is not None else os.getenv("TRAIN_ENABLE_DOP", "true")
    )
    enable_dop = parse_boolish(enable_dop_raw)
    quantize_raw = args.quantize if args.quantize is not None else os.getenv("TRAIN_QUANTIZE", "false")
    quantize = parse_boolish(quantize_raw)
    timestep_bias = args.timestep_bias or os.getenv("TRAIN_TIMESTEP_BIAS", "balanced")

    raw_resolution = args.resolution or os.getenv("TRAIN_RESOLUTION", "1024,1408")
    resolutions = parse_resolution_list(raw_resolution)

    comfyui_loras_dir_raw = args.comfyui_loras_dir or os.getenv("COMFYUI_LORAS_DIR", "")
    comfyui_loras_dir: Path | None = Path(comfyui_loras_dir_raw) if comfyui_loras_dir_raw else None

    # Infer FLUX.2-dev model path from the HF snapshot download location
    flux2_model_path = args.flux2_model_path or os.getenv("FLUX2_MODEL_PATH", "")
    if not flux2_model_path:
        models_root = os.getenv("MODELS_ROOT", "models")
        flux2_model_path = str(Path(models_root) / "base" / "flux2_dev")

    if not dataset_dir:
        raise SystemExit("dataset_dir is required (--dataset-dir or IDENTITY_DATASET_DIR env)")
    if not trigger_token:
        raise SystemExit("trigger_token is required (--trigger-token or IDENTITY_TRIGGER_TOKEN env)")

    run_id = datetime.now(timezone.utc).strftime("identity_train_%Y%m%dT%H%M%SZ")
    output_dir = base_output_dir / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    # Generate AI Toolkit YAML config
    config_script = Path(__file__).parent / "generate_training_config.py"
    config_path = output_dir / f"{run_id}_config.yaml"

    gen_cmd = [
        "python",
        str(config_script),
        "--output", str(config_path),
        "--run-name", run_id,
        "--dataset-dir", str(Path(dataset_dir) / "images"),
        "--caption-ext", "txt",
        "--output-dir", str(output_dir),
        "--trigger-token", trigger_token,
        "--caption-suffix", caption_suffix,
        "--flux2-model-path", flux2_model_path,
        "--steps", str(steps),
        "--batch-size", str(batch_size),
        "--learning-rate", str(learning_rate),
        "--rank", str(rank),
        "--weight-decay", str(weight_decay),
        "--optimizer", optimizer,
        "--enable-dop", str(enable_dop).lower(),
        "--quantize", str(quantize).lower(),
        "--timestep-bias", timestep_bias,
    ]
    for r in resolutions:
        gen_cmd.extend(["--resolution", str(r)])

    launch_manifest = {
        "run_id": run_id,
        "dataset_dir": dataset_dir,
        "output_dir": str(output_dir),
        "trigger_token": trigger_token,
        "caption_suffix": caption_suffix,
        "steps": steps,
        "batch_size": batch_size,
        "learning_rate": learning_rate,
        "rank": rank,
        "weight_decay": weight_decay,
        "optimizer": optimizer,
        "enable_dop": enable_dop,
        "quantize": quantize,
        "timestep_bias": timestep_bias,
        "resolutions": resolutions,
        "flux2_model_path": flux2_model_path,
        "ai_toolkit_root": str(ai_toolkit_root),
        "config_path": str(config_path),
        "dry_run": args.dry_run,
    }
    Path("manifests").mkdir(parents=True, exist_ok=True)
    manifest_path = Path("manifests") / f"{run_id}.json"
    manifest_path.write_text(json.dumps(launch_manifest, indent=2), encoding="utf-8")

    if args.dry_run:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "config_generation_command": gen_cmd,
                    "ai_toolkit_command": f"python {ai_toolkit_root}/run.py {config_path}",
                    "manifest": str(manifest_path),
                },
                indent=2,
            )
        )
        return 0

    # Step 1: Generate YAML config
    print(f"Generating training config: {config_path}")
    gen_result = subprocess.run(gen_cmd, check=False)
    if gen_result.returncode != 0:
        raise SystemExit(f"Config generation failed (exit {gen_result.returncode})")

    # Step 2: Validate AI Toolkit path
    run_py = ai_toolkit_root / "run.py"
    if not run_py.exists():
        raise SystemExit(
            f"AI Toolkit not found at {ai_toolkit_root}.\n"
            f"Clone it with: git clone https://github.com/ostris/ai-toolkit.git {ai_toolkit_root}\n"
            f"Then install: cd {ai_toolkit_root} && pip install -r requirements.txt"
        )

    # Resolve Python runtime for AI Toolkit itself.
    if ai_toolkit_python_arg:
        ai_toolkit_python = Path(ai_toolkit_python_arg)
    else:
        default_venv_python = ai_toolkit_root / ".venv" / "bin" / "python"
        ai_toolkit_python = default_venv_python if default_venv_python.exists() else Path("python")

    # Step 3: Run training
    train_cmd = [str(ai_toolkit_python), str(run_py), str(config_path)]
    print(f"Executing: {' '.join(train_cmd)}")
    completed = subprocess.run(train_cmd, check=False)
    if completed.returncode != 0:
        print(f"Training exited with code {completed.returncode}")
        return completed.returncode

    # Step 4: Copy latest LoRA to ComfyUI loras directory
    if comfyui_loras_dir:
        latest_lora = _find_latest_lora(output_dir)
        if latest_lora:
            comfyui_loras_dir.mkdir(parents=True, exist_ok=True)
            safe_token = trigger_token.strip("[]").replace(" ", "_")
            dest = comfyui_loras_dir / f"{safe_token}_latest.safetensors"
            shutil.copy2(latest_lora, dest)
            print(f"Copied trained LoRA to ComfyUI: {dest}")
            launch_manifest["comfyui_lora_path"] = str(dest)
            manifest_path.write_text(json.dumps(launch_manifest, indent=2), encoding="utf-8")
        else:
            print("WARNING: No .safetensors found in output dir after training.")

    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
