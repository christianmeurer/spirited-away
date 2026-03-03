#!/usr/bin/env python3
"""Generate an Ostris AI Toolkit YAML training config for FLUX.2 LoRA training.

Produces a config compatible with https://github.com/ostris/ai-toolkit
for training a FLUX.2 [dev] identity adapter LoRA.

Usage:
  python scripts/training/generate_training_config.py \\
    --output /opt/aurora/models/identity_adapters/run_id/config.yaml \\
    --run-name identity_train_20260302T120000Z \\
    --dataset-dir /opt/aurora/data/identity_dataset/images \\
    --caption-ext txt \\
    --output-dir /opt/aurora/models/identity_adapters/identity_train_20260302T120000Z \\
    --trigger-token "[subj_name_2026]" \\
    --caption-suffix "person" \\
    --flux2-model-path /opt/aurora/models/base/flux2_dev \\
    --steps 2500 \\
    --batch-size 2 \\
    --learning-rate 0.0001 \\
    --rank 32 \\
    --weight-decay 0.0001 \\
    --optimizer AdamW8Bit \\
    --enable-dop true \\
    --timestep-bias balanced \\
    --resolution 1024 --resolution 1408
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def build_config(
    run_name: str,
    dataset_dir: str,
    caption_ext: str,
    output_dir: str,
    trigger_token: str,
    caption_suffix: str,
    flux2_model_path: str,
    steps: int,
    batch_size: int,
    learning_rate: float,
    rank: int,
    weight_decay: float,
    optimizer: str,
    enable_dop: bool,
    timestep_bias: str,
    quantize: bool,
    resolutions: list[int],
    sample_prompts: list[str] | None = None,
) -> str:
    """Render the AI Toolkit YAML configuration as a string."""

    if sample_prompts is None:
        sample_prompts = [f"{trigger_token} {caption_suffix}"]

    # Build resolution list for YAML (indented block sequence)
    resolution_lines = "\n".join(f"            - {r}" for r in resolutions)

    # Build sample prompts YAML block
    prompt_lines = "\n".join(f'          - "{p}"' for p in sample_prompts)

    # Differential Output Preservation flag
    # AI Toolkit calls this "do_cfg" / "do_diff_guide" depending on version;
    # the canonical field in current ai-toolkit is `differential_output_preservation`.
    dop_field = "true" if enable_dop else "false"
    quantize_field = "true" if quantize else "false"

    # Timestep bias: map our convention to ai-toolkit's sampler_bias setting
    # "balanced" → no bias; "low" → favor low-noise timesteps
    if timestep_bias == "low":
        timestep_cfg = (
            "        timestep_type: logit_normal\n"
            "        timestep_mu: -1.0\n"
            "        timestep_sigma: 1.0"
        )
    elif timestep_bias == "high":
        timestep_cfg = (
            "        timestep_type: logit_normal\n"
            "        timestep_mu: 1.0\n"
            "        timestep_sigma: 1.0"
        )
    else:
        # balanced
        timestep_cfg = (
            "        timestep_type: logit_normal\n"
            "        timestep_mu: 0.0\n"
            "        timestep_sigma: 1.0"
        )

    config = f"""\
job: extension
config:
  name: {run_name}
  process:
    - type: sd_trainer
      training_folder: {output_dir}
      device: cuda:0
      trigger_word: "{trigger_token}"

      network:
        type: lora
        linear: {rank}
        linear_alpha: {rank}

      save:
        dtype: float16
        save_every: 250
        max_step_saves_to_keep: 4

      datasets:
        - folder_path: {dataset_dir}
          caption_ext: {caption_ext}
          caption_dropout_rate: 0.05
          shuffle_tokens: false
          cache_latents_to_disk: true
          resolution:
{resolution_lines}

      train:
        batch_size: {batch_size}
        steps: {steps}
        gradient_accumulation_steps: 1
        train_unet: true
        train_text_encoder: false
        gradient_checkpointing: true
        noise_scheduler: flowmatch
{timestep_cfg}
        optimizer: {optimizer}
        optimizer_params:
          weight_decay: {weight_decay}
        learning_rate: {learning_rate}
        lr_scheduler: constant
        target_modules: all-linear
        differential_output_preservation: {dop_field}
        ema_config:
          use_ema: false
        dtype: bf16

      model:
        name_or_path: {flux2_model_path}
        is_flux: true
        quantize: {quantize_field}

      sample:
        sampler: flowmatch
        sample_every: 500
        width: {resolutions[0]}
        height: {resolutions[0]}
        prompts:
{prompt_lines}
        neg: ""
        seed: 42
        walk_seed: true
        guidance_scale: 3.5
        sample_steps: 28
"""
    return config


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate AI Toolkit YAML config for FLUX.2 LoRA")
    parser.add_argument("--output", required=True, help="Output YAML file path")
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--dataset-dir", required=True, help="Directory containing image files")
    parser.add_argument("--caption-ext", default="txt", help="Caption file extension (default: txt)")
    parser.add_argument("--output-dir", required=True, help="Training output directory")
    parser.add_argument("--trigger-token", required=True)
    parser.add_argument("--caption-suffix", default="person")
    parser.add_argument(
        "--flux2-model-path",
        required=True,
        help="Path to FLUX.2-dev snapshot dir or HuggingFace repo ID",
    )
    parser.add_argument("--steps", type=int, default=2500)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--learning-rate", type=float, default=0.0001)
    parser.add_argument("--rank", type=int, default=32)
    parser.add_argument("--weight-decay", type=float, default=0.0001)
    parser.add_argument("--optimizer", default="AdamW8Bit")
    parser.add_argument("--enable-dop", default="true")
    parser.add_argument(
        "--quantize",
        default="false",
        help="Enable model quantization in ai-toolkit config (true/false). Default: false",
    )
    parser.add_argument("--timestep-bias", default="balanced", choices=["balanced", "low", "high"])
    parser.add_argument(
        "--resolution",
        action="append",
        dest="resolutions",
        type=int,
        metavar="INT",
        help="Training resolution (can be specified multiple times). Default: 1024 1408",
    )
    parser.add_argument(
        "--sample-prompt",
        action="append",
        dest="sample_prompts",
        help="Additional sample prompt (can be specified multiple times)",
    )
    args = parser.parse_args()

    enable_dop = str(args.enable_dop).strip().lower() in {"1", "true", "yes", "on"}
    quantize = str(args.quantize).strip().lower() in {"1", "true", "yes", "on"}
    resolutions = args.resolutions or [1024, 1408]
    sample_prompts = args.sample_prompts  # None → default inside build_config

    config_yaml = build_config(
        run_name=args.run_name,
        dataset_dir=args.dataset_dir,
        caption_ext=args.caption_ext,
        output_dir=args.output_dir,
        trigger_token=args.trigger_token,
        caption_suffix=args.caption_suffix,
        flux2_model_path=args.flux2_model_path,
        steps=args.steps,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        rank=args.rank,
        weight_decay=args.weight_decay,
        optimizer=args.optimizer,
        enable_dop=enable_dop,
        timestep_bias=args.timestep_bias,
        quantize=quantize,
        resolutions=resolutions,
        sample_prompts=sample_prompts,
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(config_yaml, encoding="utf-8")
    print(f"Training config written: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
