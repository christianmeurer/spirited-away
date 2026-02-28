#!/usr/bin/env python3
"""Fetch models from Hugging Face Hub using a pinned registry and emit a lockfile."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from huggingface_hub import hf_hub_download, model_info, snapshot_download


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


def fetch_model(model: dict[str, Any], models_root: Path, token: str | None) -> dict[str, Any]:
    alias = model["alias"]
    repo_id = model["repo_id"]
    revision = model.get("revision", "main")
    local_dir = models_root / model.get("local_dir", alias)
    ensure_dir(local_dir)

    info = model_info(repo_id=repo_id, revision=revision, token=token)
    resolved_commit = info.sha

    download_cfg = model.get("download", {})
    mode = download_cfg.get("mode", "snapshot")

    if mode == "files":
        downloaded_files = []
        for filename in download_cfg.get("files", []):
            out_path = hf_hub_download(
                repo_id=repo_id,
                filename=filename,
                revision=revision,
                token=token,
                local_dir=str(local_dir),
                local_dir_use_symlinks=False,
            )
            downloaded_files.append(out_path)
    else:
        snapshot_download(
            repo_id=repo_id,
            revision=revision,
            token=token,
            local_dir=str(local_dir),
            local_dir_use_symlinks=False,
            allow_patterns=download_cfg.get("allow_patterns"),
            ignore_patterns=download_cfg.get("ignore_patterns"),
        )
        downloaded_files = []

    return {
        "alias": alias,
        "repo_id": repo_id,
        "revision_requested": revision,
        "resolved_commit": resolved_commit,
        "local_dir": str(local_dir),
        "mode": mode,
        "downloaded_files": downloaded_files,
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
        print("Warning: HF_TOKEN is not set. Public models only.")

    fetched: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for model in registry.get("models", []):
        required = bool(model.get("required", False))
        try:
            result = fetch_model(model, models_root=models_root, token=token)
            fetched.append(result)
            print(f"Fetched: {result['alias']} ({result['resolved_commit']})")
        except Exception as exc:  # noqa: BLE001
            failure = {
                "alias": model.get("alias"),
                "repo_id": model.get("repo_id"),
                "error": str(exc),
                "required": required,
            }
            failures.append(failure)
            print(f"Failed: {failure['alias']} -> {failure['error']}")
            if required or not args.allow_optional_failures:
                payload = {
                    "valid": False,
                    "registry": str(registry_path),
                    "fetched": fetched,
                    "failures": failures,
                }
                Path(args.output_lock).write_text(json.dumps(payload, indent=2), encoding="utf-8")
                return 1

    payload = {
        "valid": len([f for f in failures if f.get("required")]) == 0,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "registry": str(registry_path),
        "fetched": fetched,
        "failures": failures,
    }
    Path(args.output_lock).write_text(json.dumps(payload, indent=2), encoding="utf-8")

    timestamped = Path("manifests") / f"model_lock.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    timestamped.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"Lockfile written: {args.output_lock}")
    print(f"Timestamped lockfile written: {timestamped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

