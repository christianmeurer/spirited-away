#!/usr/bin/env python3
"""Acquire character references from approved sources with rights safeguards."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download_file(url: str, out_path: Path, timeout: int = 120) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=timeout) as response:
        response.raise_for_status()
        with out_path.open("wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)


def main() -> int:
    parser = argparse.ArgumentParser(description="Acquire Spirited Away character reference assets")
    parser.add_argument(
        "--source-manifest",
        default="configs/characters/spirited_away_sources.internal_rnd.json",
        help="Source definition manifest",
    )
    parser.add_argument(
        "--output-dir",
        default="data/character_refs",
        help="Output directory for acquired assets",
    )
    args = parser.parse_args()

    manifest = json.loads(Path(args.source_manifest).read_text(encoding="utf-8"))
    policy = manifest.get("policy", {})
    allowed_kinds = set(policy.get("allowed_source_kinds", []))
    allowed_domains = set(policy.get("allowed_domains", []))

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    Path("manifests").mkdir(parents=True, exist_ok=True)

    acquired = []
    errors = []

    for asset in manifest.get("assets", []):
        asset_id = asset.get("asset_id")
        source_kind = asset.get("source_kind")
        rights = asset.get("rights", {})

        if source_kind not in allowed_kinds:
            errors.append(f"{asset_id}: source_kind '{source_kind}' is not allowed")
            continue

        if policy.get("require_rights_attestation"):
            if not rights.get("status") or not rights.get("evidence"):
                errors.append(f"{asset_id}: rights attestation is required")
                continue

        try:
            if source_kind == "licensed_url":
                source_url = asset.get("source_url", "")
                domain = urlparse(source_url).netloc
                if domain not in allowed_domains:
                    raise ValueError(f"domain '{domain}' is not allowed")

                suffix = Path(urlparse(source_url).path).suffix or ".jpg"
                dst = output_dir / f"{asset_id}{suffix.lower()}"
                download_file(source_url, dst)
            elif source_kind == "manual_upload":
                source_path = asset.get("source_path", "")
                src = Path(source_path)
                if not src.exists():
                    raise FileNotFoundError(f"manual source path not found: {src}")
                dst = output_dir / f"{asset_id}{src.suffix.lower()}"
                shutil.copy2(src, dst)
            else:
                raise ValueError(f"Unsupported source_kind: {source_kind}")

            acquired.append(
                {
                    "asset_id": asset_id,
                    "character": asset.get("character"),
                    "path": str(dst),
                    "sha256": sha256_file(dst),
                    "rights": rights,
                    "notes": asset.get("notes"),
                }
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{asset_id}: {exc}")

    report = {
        "valid": len(errors) == 0,
        "source_manifest": args.source_manifest,
        "output_dir": str(output_dir),
        "acquired": acquired,
        "errors": errors,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    out_path = Path("manifests") / f"character_assets_acquisition.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

