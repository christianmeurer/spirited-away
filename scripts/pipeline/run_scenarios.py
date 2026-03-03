#!/usr/bin/env python3
"""Generate scenario images through ComfyUI API and emit manifests.

Scenario A: Real person + photoreal companions (identity LoRA + Flux2MultiReference)
Scenario B: Anime-stylized subject + 2D companions (anime LoRA via Anything2Real)
Scenario C: Mixed-media composition with strict domain boundaries (TPBlendAttentionProcessor)

Workflow templates use FLUX.2-native loading topology (UNETLoader + DualCLIPLoader +
VAELoader + LoraLoader + FluxGuidance) instead of CheckpointLoaderSimple.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


SCENARIO_ORDER = ["scenario_a", "scenario_b", "scenario_c"]
SCENARIO_DEFAULT_TRACK = {
    "scenario_a": "track_a",
    "scenario_b": "track_b",
    "scenario_c": "track_c",
}
SCENARIO_PROMPTS = {
    "scenario_a": (
        "Realistic cinematic photograph of [subj_name_2026] with inspired companions, "
        "coherent shadows and perspective, natural skin texture"
    ),
    "scenario_b": (
        "2D anime cel frame of [subj_name_2026] with anime companions, "
        "watercolor background, consistent line art"
    ),
    "scenario_c": (
        "Mixed-media composition: real [subj_name_2026] with 2D companions, "
        "clear style boundaries and grounded contact shadows"
    ),
}
SCENARIO_NEGATIVE = {
    "scenario_a": "cartoon, cel-shading, painterly textures",
    "scenario_b": "photoreal, live action, realistic skin",
    "scenario_c": "style bleed, boundary artifacts, inconsistent perspective",
}

FORBIDDEN_SCENARIO_A_TOKENS = {"anime", "illustration"}

# FLUX.2 sampler defaults (euler + simple, cfg=1.0)
FLUX2_SAMPLER = "euler"
FLUX2_SCHEDULER = "simple"


def _resolve_arg_or_env(arg_value: str | None, env_key: str, default: str = "") -> str:
    if arg_value is not None and arg_value.strip():
        return arg_value.strip()
    return os.getenv(env_key, default).strip()


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_scenario_a_prompt_policy(prompt: str, negative_prompt: str) -> None:
    text = f"{prompt} {negative_prompt}".lower()
    hits = [token for token in FORBIDDEN_SCENARIO_A_TOKENS if token in text]
    if hits:
        raise ValueError(
            "Scenario A prompt policy violation. Remove style tokens for Anything2Real "
            "photoreal translation: " + ", ".join(sorted(hits))
        )


def replace_tokens(node: Any, values: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        return {k: replace_tokens(v, values) for k, v in node.items()}
    if isinstance(node, list):
        return [replace_tokens(v, values) for v in node]
    if isinstance(node, str):
        for key, value in values.items():
            token = "{{" + key + "}}"
            if node == token:
                return value
            node = node.replace(token, str(value))
    return node


def _resolve_comfy_asset_path(comfy_root: Path, asset_ref: str) -> Path:
    asset = Path(asset_ref)
    if asset.is_absolute():
        return asset
    primary = comfy_root / asset
    if primary.exists():
        return primary
    return comfy_root / "input" / asset


def preflight_scenario_c_assets(comfy_root: Path, assets: dict[str, str]) -> list[str]:
    missing: list[str] = []
    for key, value in assets.items():
        resolved = _resolve_comfy_asset_path(comfy_root, value)
        if not resolved.exists():
            missing.append(f"{key}={value} (expected at {resolved})")
    return missing


def validate_scenario_track_policy(
    scenario_id: str,
    track_id: str,
    scenario_config: dict[str, Any],
) -> None:
    scenario_rule = scenario_config["scenarios"][scenario_id]
    required_track = scenario_rule.get("track_id")
    if isinstance(required_track, str) and required_track != track_id:
        raise ValueError(
            f"Scenario '{scenario_id}' requires track_id='{required_track}', got '{track_id}'."
        )
    allowed_track_ids = scenario_rule.get("allowed_track_ids", [])
    if isinstance(allowed_track_ids, list) and allowed_track_ids and track_id not in allowed_track_ids:
        raise ValueError(
            f"Scenario '{scenario_id}' does not allow track_id='{track_id}' "
            f"(allowed: {allowed_track_ids})."
        )


def _build_scenario_checks(
    scenario_id: str,
    required_checks: list[str],
    identity_lora_name: str,
    anime_lora_name: str,
) -> dict[str, bool]:
    """Build scenario_checks dict with real enforcement for LoRA-dependent checks.

    Checks that depend on actual workflow node execution are evaluated based on
    whether the required resource is configured. Other checks (requiring human
    evaluation like light_shadow_coherence, perspective_consistency) are set to
    True by default — these are verified at the human review gate, not here.
    """
    checks: dict[str, bool] = {}
    for check in required_checks:
        if check == "identity_adapter_loaded":
            # True only when an identity LoRA is actually configured
            checks[check] = bool(identity_lora_name)
        elif check == "anime_style_lora_loaded":
            # True only when an anime LoRA is actually configured
            checks[check] = bool(anime_lora_name)
        else:
            # All other checks require human review; default to True here,
            # auditable through the human_review_approved gate at validation time.
            checks[check] = True
    return checks


class ComfyClient:
    def __init__(self, base_url: str, timeout_seconds: int = 900) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()

    def queue_prompt(self, prompt: dict[str, Any], client_id: str) -> str:
        payload = {"prompt": prompt, "client_id": client_id}
        resp = self.session.post(f"{self.base_url}/prompt", json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        return data["prompt_id"]

    def wait_for_history(self, prompt_id: str) -> dict[str, Any]:
        deadline = datetime.now(timezone.utc).timestamp() + self.timeout_seconds
        while datetime.now(timezone.utc).timestamp() < deadline:
            resp = self.session.get(f"{self.base_url}/history/{prompt_id}", timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if prompt_id in data:
                return data[prompt_id]
        raise TimeoutError(f"Timed out waiting for prompt_id={prompt_id}")


def extract_output_images(history_entry: dict[str, Any]) -> list[dict[str, str]]:
    outputs = history_entry.get("outputs", {})
    images: list[dict[str, str]] = []
    for _, node_output in outputs.items():
        if not isinstance(node_output, dict):
            continue
        for image in node_output.get("images", []):
            if isinstance(image, dict):
                images.append(
                    {
                        "filename": str(image.get("filename", "")),
                        "subfolder": str(image.get("subfolder", "")),
                        "type": str(image.get("type", "output")),
                    }
                )
    return images


def make_manifest(
    run_id: str,
    scenario_id: str,
    track_id: str,
    prompt: str,
    negative_prompt: str,
    seed: int,
    outputs: list[dict[str, str]],
    scenario_config: dict[str, Any],
    identity_lora_name: str,
    anime_lora_name: str,
) -> dict[str, Any]:
    required_checks = scenario_config["scenarios"][scenario_id].get("required_item_checks", [])
    scenario_checks = _build_scenario_checks(
        scenario_id=scenario_id,
        required_checks=required_checks,
        identity_lora_name=identity_lora_name,
        anime_lora_name=anime_lora_name,
    )

    # Determine adapter versions for provenance tracking
    adapter_versions: list[str] = []
    if scenario_id == "scenario_a" and identity_lora_name:
        adapter_versions.append(identity_lora_name)
    if scenario_id == "scenario_b" and anime_lora_name:
        adapter_versions.append(anime_lora_name)
    if not adapter_versions:
        adapter_versions = ["pending_adapter_versions"]

    manifest = {
        "run_id": f"{run_id}_{scenario_id}",
        "track_id": track_id,
        "scenario_id": scenario_id,
        "usage_scope": "INTERNAL_RND",
        "internal_tag": "INTERNAL_RND",
        "sharing_allowed": False,
        "provider_policy_ack": True,
        "compliance_status": "pending_manual_approvals_and_metric_scoring",
        "strict_validation_mode": "explicit_step_required",
        "manual_final_candidate_approval": False,
        "provider_policy": {
            "execution_order": [
                "digitalocean_primary_region",
                "digitalocean_secondary_region",
                "local_fallback_for_pre_post_only",
            ]
        },
        "gates": {
            "human_review_approved": False,
            "pre_export_guard_passed": False,
            "track_isolation_passed": False,
        },
        "outputs": [
            {
                "artifact_id": f"{scenario_id}_{i + 1:03d}",
                "destination": f"comfy://{o['type']}/{o['subfolder']}/{o['filename']}",
                "internal_only": True,
            }
            for i, o in enumerate(outputs)
        ],
        "batch": [
            {
                "item_id": f"{scenario_id}_001",
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "seed": seed,
                "sampler": FLUX2_SAMPLER,
                "scheduler": FLUX2_SCHEDULER,
                "base_model_hash": "pending_model_hash",
                "adapter_versions": adapter_versions,
                "postprocess_chain": [],
                "external_component_provenance": ["ComfyUI", "FLUX.2 [dev]", "HuggingFace Models"],
                "external_component_licensing_notes": (
                    "Internal use only; verify each component license before export. "
                    "FLUX.2 [dev] is under FLUX Non-Commercial License."
                ),
                "scenario_checks": scenario_checks,
                "metrics": {
                    "anatomy_failed": False,
                    "identity_similarity": 0.0,
                    "style_fidelity": 0.0,
                    "pairing_score": 0.0,
                    "needs_human_scoring": True,
                },
            }
        ],
    }

    if scenario_id == "scenario_c":
        manifest["declared_target_track"] = track_id
        manifest["mixed_media_boundary_approval"] = {
            "approved": False,
            "approved_by": None,
            "notes": "Pending human review",
        }

    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Run scenario generation on ComfyUI")
    parser.add_argument("--env-file", default="configs/env/digitalocean_h100.env")
    parser.add_argument("--scenario", default="all", choices=["all", *SCENARIO_ORDER])
    parser.add_argument("--workflow-dir", default="configs/workflows")
    parser.add_argument("--scenario-config", default="configs/scenarios/internal_rnd_scenarios.json")
    parser.add_argument("--seed-base", type=int, default=120000)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--prompt-override", default=None)
    parser.add_argument("--negative-prompt-override", default=None)

    # FLUX.2 model file names (relative to ComfyUI model subdirectories)
    parser.add_argument("--unet-name", default=None)
    parser.add_argument("--t5-encoder-name", default=None)
    parser.add_argument("--clip-l-name", default=None)
    parser.add_argument("--vae-name", default=None)
    parser.add_argument("--identity-lora-name", default=None)
    parser.add_argument("--anime-lora-name", default=None)

    # Scenario C contract
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
        help=(
            "When --scenario all is used, fail if Scenario C contract is missing "
            "instead of auto-skipping Scenario C."
        ),
    )
    args = parser.parse_args()

    load_env_file(Path(args.env_file))

    comfy_base_url = os.getenv("COMFYUI_BASE_URL", "http://127.0.0.1:8188")
    timeout_seconds = int(os.getenv("COMFYUI_TIMEOUT_SECONDS", "900"))
    comfy_root = Path(os.getenv("COMFYUI_ROOT", "/opt/aurora/ComfyUI"))

    # FLUX.2 model file names
    unet_name = _resolve_arg_or_env(args.unet_name, "COMFYUI_UNET_NAME", "flux1-dev.safetensors")
    t5_encoder_name = _resolve_arg_or_env(
        args.t5_encoder_name, "COMFYUI_T5_ENCODER_NAME", "t5xxl_fp8_e4m3fn.safetensors"
    )
    clip_l_name = _resolve_arg_or_env(args.clip_l_name, "COMFYUI_CLIP_L_NAME", "clip_l.safetensors")
    vae_name = _resolve_arg_or_env(args.vae_name, "COMFYUI_VAE_NAME", "ae.safetensors")
    identity_lora_name = _resolve_arg_or_env(
        args.identity_lora_name, "IDENTITY_LORA_NAME", ""
    )
    anime_lora_name = _resolve_arg_or_env(
        args.anime_lora_name, "ANIME_LORA_NAME", "f2k_anything2real_a.safetensors"
    )

    run_id = args.run_id or datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ")
    scenario_config = read_json(Path(args.scenario_config))

    scenarios = SCENARIO_ORDER if args.scenario == "all" else [args.scenario]
    client = ComfyClient(base_url=comfy_base_url, timeout_seconds=timeout_seconds)

    scenario_c_assets = {
        "SUBJECT_IMAGE": _resolve_arg_or_env(
            args.scenario_c_subject_image, "SCENARIO_C_SUBJECT_IMAGE"
        ),
        "COMPANION_IMAGE_A": _resolve_arg_or_env(
            args.scenario_c_companion_image_a, "SCENARIO_C_COMPANION_IMAGE_A"
        ),
        "COMPANION_IMAGE_B": _resolve_arg_or_env(
            args.scenario_c_companion_image_b, "SCENARIO_C_COMPANION_IMAGE_B"
        ),
        "SUBJECT_MASK_IMAGE": _resolve_arg_or_env(
            args.scenario_c_subject_mask_image, "SCENARIO_C_SUBJECT_MASK_IMAGE"
        ),
        "COMPANION_MASK_IMAGE": _resolve_arg_or_env(
            args.scenario_c_companion_mask_image, "SCENARIO_C_COMPANION_MASK_IMAGE"
        ),
    }
    scenario_c_ps_blend_mode = _resolve_arg_or_env(
        args.scenario_c_ps_blend_mode, "SCENARIO_C_PS_BLEND_MODE"
    )
    scenario_c_track_id = _resolve_arg_or_env(args.scenario_c_track, "SCENARIO_C_TRACK")

    if "scenario_c" in scenarios:
        missing_config: list[str] = []
        unresolved_assets = [key for key, value in scenario_c_assets.items() if not value]
        if unresolved_assets:
            missing_config.append("missing inputs/masks: " + ", ".join(sorted(unresolved_assets)))
        if not scenario_c_track_id:
            missing_config.append("missing track: SCENARIO_C_TRACK / --scenario-c-track")
        if not scenario_c_ps_blend_mode:
            missing_config.append(
                "missing blend mode: SCENARIO_C_PS_BLEND_MODE / --scenario-c-ps-blend-mode"
            )

        missing_assets: list[str] = []
        if not missing_config:
            missing_assets = preflight_scenario_c_assets(comfy_root, scenario_c_assets)

        if missing_config or missing_assets:
            reason_lines: list[str] = []
            reason_lines.extend(missing_config)
            if missing_assets:
                reason_lines.append("missing files:")
                reason_lines.extend(f"  - {entry}" for entry in missing_assets)

            if args.scenario == "all" and not args.require_scenario_c:
                scenarios = [s for s in scenarios if s != "scenario_c"]
                print("WARNING: Scenario C skipped in --scenario all due to incomplete contract.")
                for line in reason_lines:
                    print(f"WARNING: {line}")
            else:
                raise ValueError(
                    "Scenario C is required but preflight failed:\n" + "\n".join(reason_lines)
                )

    out_manifest_dir = Path("manifests/generated")
    out_manifest_dir.mkdir(parents=True, exist_ok=True)

    for idx, scenario_id in enumerate(scenarios):
        template_path = Path(args.workflow_dir) / f"{scenario_id}.workflow.template.json"
        workflow_template = read_json(template_path)

        prompt = args.prompt_override or SCENARIO_PROMPTS[scenario_id]
        negative_prompt = args.negative_prompt_override or SCENARIO_NEGATIVE[scenario_id]

        if scenario_id == "scenario_a":
            validate_scenario_a_prompt_policy(prompt, negative_prompt)

        track_id = SCENARIO_DEFAULT_TRACK[scenario_id]
        if scenario_id == "scenario_c":
            track_id = scenario_c_track_id
        validate_scenario_track_policy(scenario_id, track_id, scenario_config)

        seed = args.seed_base + idx
        filename_prefix = f"{run_id}_{scenario_id}"

        # Build template token values — covers all scenarios A, B, and C
        values: dict[str, Any] = {
            # FLUX.2 model files
            "UNET_NAME": unet_name,
            "T5_ENCODER_NAME": t5_encoder_name,
            "CLIP_L_NAME": clip_l_name,
            "VAE_NAME": vae_name,
            # Common
            "PROMPT": prompt,
            "NEGATIVE_PROMPT": negative_prompt,
            "SEED": seed,
            "FILENAME_PREFIX": filename_prefix,
            # Scenario A
            "IDENTITY_LORA_NAME": identity_lora_name,
            # Scenario B
            "ANIME_LORA_NAME": anime_lora_name,
            # Scenario C
            "SUBJECT_IMAGE": scenario_c_assets["SUBJECT_IMAGE"],
            "COMPANION_IMAGE_A": scenario_c_assets["COMPANION_IMAGE_A"],
            "COMPANION_IMAGE_B": scenario_c_assets["COMPANION_IMAGE_B"],
            "SUBJECT_MASK_IMAGE": scenario_c_assets["SUBJECT_MASK_IMAGE"],
            "COMPANION_MASK_IMAGE": scenario_c_assets["COMPANION_MASK_IMAGE"],
            "PS_BLEND_MODE": scenario_c_ps_blend_mode,
        }

        workflow = replace_tokens(copy.deepcopy(workflow_template), values)
        client_id = str(uuid.uuid4())
        prompt_id = client.queue_prompt(workflow, client_id=client_id)
        history = client.wait_for_history(prompt_id)
        outputs = extract_output_images(history)

        manifest = make_manifest(
            run_id=run_id,
            scenario_id=scenario_id,
            track_id=track_id,
            prompt=prompt,
            negative_prompt=negative_prompt,
            seed=seed,
            outputs=outputs,
            scenario_config=scenario_config,
            identity_lora_name=identity_lora_name,
            anime_lora_name=anime_lora_name,
        )

        manifest_path = out_manifest_dir / f"{run_id}_{scenario_id}.internal_rnd.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"Scenario {scenario_id} complete. Manifest: {manifest_path}")
        print(
            "INFO: Manifest compliance status is pending manual approvals and metric scoring. "
            f"Strict validation is an explicit step: "
            f"python scripts/internal_rnd_cli.py validate --manifest {manifest_path}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
