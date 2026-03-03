#!/usr/bin/env python3
"""Fetch models from Hugging Face Hub using a pinned registry and emit a lockfile.

Handles gated model access with explicit error messages directing operators to
the HuggingFace license-acceptance page rather than surfacing cryptic HTTP 403s.

Only the top-level "models" array is downloaded via HuggingFace Hub.
Entries in "api_only_models" and "optional_llm_models" are recorded in the
lockfile as metadata but not downloaded (they use separate access paths).

After download, FLUX.2-dev components are automatically symlinked into the
correct ComfyUI model subdirectory layout if COMFYUI_ROOT is set in the env.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from huggingface_hub import hf_hub_download, model_info, snapshot_download
from huggingface_hub.utils import (
    GatedRepoError,
    RepositoryNotFoundError,
    RevisionNotFoundError,
)


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _gated_error_message(repo_id: str, license_url: str | None) -> str:
    url = license_url or f"https://huggingface.co/{repo_id}"
    return (
        f"Access denied for gated model '{repo_id}'.\n"
        f"  → Accept the license agreement at: {url}\n"
        f"  → Log in to the HuggingFace account whose token is set in HF_TOKEN.\n"
        f"  → After acceptance, re-run this script."
    )


def _link_flux2_to_comfyui(local_dir: Path, comfyui_root: Path) -> list[str]:
    """Symlink FLUX.2-dev components into ComfyUI's expected directory layout.

    FLUX.2-dev snapshot contains:
      - transformer/diffusion_pytorch_model*.safetensors → diffusion_models/flux1-dev.safetensors
      - text_encoder/model.safetensors                  → text_encoders/clip_l.safetensors
      - text_encoder_2/model.safetensors                → text_encoders/t5xxl_fp8_e4m3fn.safetensors
      - ae.safetensors                                  → vae/ae.safetensors
    """
    notes: list[str] = []

    mapping = [
        # (source_glob_pattern, dest_subdir, dest_filename)
        ("transformer/diffusion_pytorch_model*.safetensors", "diffusion_models", "flux1-dev.safetensors"),
        ("text_encoder/model.safetensors", "text_encoders", "clip_l.safetensors"),
        ("text_encoder_2/model.safetensors", "text_encoders", "t5xxl_fp8_e4m3fn.safetensors"),
        ("ae.safetensors", "vae", "ae.safetensors"),
    ]

    for pattern, dest_subdir, dest_name in mapping:
        matches = sorted(local_dir.glob(pattern))
        if not matches:
            notes.append(f"FLUX.2 layout: no match for {pattern} in {local_dir}")
            continue
        src = matches[0]
        dest_dir = comfyui_root / "models" / dest_subdir
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / dest_name
        if dest.exists() or dest.is_symlink():
            notes.append(f"FLUX.2 layout: {dest} already exists, skipping symlink")
            continue
        dest.symlink_to(src.resolve())
        notes.append(f"FLUX.2 layout: linked {src.name} → {dest}")

    return notes


def fetch_model(
    model: dict[str, Any],
    models_root: Path,
    token: str | None,
    comfyui_root: Path | None,
) -> dict[str, Any]:
    alias = model["alias"]
    repo_id = model["repo_id"]
    revision = model.get("revision", "main")
    local_dir = models_root / model.get("local_dir", alias)
    gated = bool(model.get("gated", False))
    license_url = model.get("license_url")
    ensure_dir(local_dir)

    try:
        info = model_info(repo_id=repo_id, revision=revision, token=token)
    except GatedRepoError as exc:
        raise RuntimeError(_gated_error_message(repo_id, license_url)) from exc
    except RepositoryNotFoundError as exc:
        raise RuntimeError(
            f"Repository not found: '{repo_id}'. "
            f"Verify the repo_id in the model registry is correct."
        ) from exc
    except RevisionNotFoundError as exc:
        raise RuntimeError(
            f"Revision '{revision}' not found in '{repo_id}'. "
            f"Check the 'revision' field in the model registry."
        ) from exc

    resolved_commit = info.sha

    download_cfg = model.get("download", {})
    mode = download_cfg.get("mode", "snapshot")
    layout_notes: list[str] = []

    if mode == "files":
        downloaded_files = []
        for filename in download_cfg.get("files", []):
            try:
                out_path = hf_hub_download(
                    repo_id=repo_id,
                    filename=filename,
                    revision=revision,
                    token=token,
                    local_dir=str(local_dir),
                    local_dir_use_symlinks=False,
                )
                downloaded_files.append(out_path)
            except GatedRepoError as exc:
                raise RuntimeError(_gated_error_message(repo_id, license_url)) from exc
    else:
        try:
            snapshot_download(
                repo_id=repo_id,
                revision=revision,
                token=token,
                local_dir=str(local_dir),
                local_dir_use_symlinks=False,
                allow_patterns=download_cfg.get("allow_patterns"),
                ignore_patterns=download_cfg.get("ignore_patterns"),
            )
        except GatedRepoError as exc:
            raise RuntimeError(_gated_error_message(repo_id, license_url)) from exc
        downloaded_files = []

        # Post-download: link FLUX.2-dev components into ComfyUI layout
        if alias == "flux2_dev" and comfyui_root and comfyui_root.is_dir():
            layout_notes = _link_flux2_to_comfyui(local_dir, comfyui_root)
            for note in layout_notes:
                print(f"  {note}")

    return {
        "alias": alias,
        "repo_id": repo_id,
        "revision_requested": revision,
        "resolved_commit": resolved_commit,
        "local_dir": str(local_dir),
        "mode": mode,
        "gated": gated,
        "downloaded_files": downloaded_files,
        "layout_notes": layout_notes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Hugging Face models from registry")
    parser.add_argument(
        "--env-file",
        default="configs/env/digitalocean_h100.env",
        help="Path to env file",
    )
    parser.add_argument(
        "--registry",
        default=None,
        help="Registry JSON path. Defaults to HF_MODEL_REGISTRY env or configs/models/hf_models.internal_rnd.json",
    )
    parser.add_argument(
        "--output-lock",
        default="manifests/model_lock.latest.json",
        help="Lockfile output path",
    )
    parser.add_argument(
        "--allow-optional-failures",
        action="store_true",
        help="Continue when optional model download fails",
    )
    parser.add_argument(
        "--link-to-comfyui",
        action="store_true",
        help="Symlink downloaded FLUX.2-dev components into COMFYUI_ROOT model directories",
    )
    args = parser.parse_args()

    load_env_file(Path(args.env_file))

    registry_path = Path(
        args.registry
        or os.getenv("HF_MODEL_REGISTRY")
        or "configs/models/hf_models.internal_rnd.json"
    )
    registry = read_json(registry_path)

    models_root = Path(os.getenv("MODELS_ROOT", "models"))
    ensure_dir(models_root)
    ensure_dir(Path("manifests"))

    token = os.getenv("HF_TOKEN")
    if not token:
        print("WARNING: HF_TOKEN is not set. Gated models will fail. Public models only.")

    comfyui_root: Path | None = None
    if args.link_to_comfyui or os.getenv("COMFYUI_ROOT"):
        comfyui_root = Path(os.getenv("COMFYUI_ROOT", "/opt/aurora/ComfyUI"))

    fetched: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for model in registry.get("models", []):
        required = bool(model.get("required", False))
        try:
            result = fetch_model(
                model,
                models_root=models_root,
                token=token,
                comfyui_root=comfyui_root,
            )
            fetched.append(result)
            print(f"Fetched: {result['alias']} ({result['resolved_commit'][:8]})")
        except Exception as exc:  # noqa: BLE001
            failure = {
                "alias": model.get("alias"),
                "repo_id": model.get("repo_id"),
                "error": str(exc),
                "required": required,
            }
            failures.append(failure)
            print(f"Failed: {failure['alias']}")
            print(f"  {failure['error']}")
            if required or not args.allow_optional_failures:
                payload = {
                    "valid": False,
                    "registry": str(registry_path),
                    "fetched": fetched,
                    "failures": failures,
                }
                Path(args.output_lock).write_text(
                    json.dumps(payload, indent=2), encoding="utf-8"
                )
                return 1

    # Record API-only and optional LLM entries in lockfile for documentation
    api_only = registry.get("api_only_models", [])
    optional_llm = registry.get("optional_llm_models", [])

    payload = {
        "valid": len([f for f in failures if f.get("required")]) == 0,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "registry": str(registry_path),
        "fetched": fetched,
        "failures": failures,
        "api_only_models": api_only,
        "optional_llm_models": optional_llm,
    }
    Path(args.output_lock).write_text(json.dumps(payload, indent=2), encoding="utf-8")

    timestamped = (
        Path("manifests")
        / f"model_lock.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    timestamped.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"Lockfile written: {args.output_lock}")
    print(f"Timestamped lockfile written: {timestamped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
