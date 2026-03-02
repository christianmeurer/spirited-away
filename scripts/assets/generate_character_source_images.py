#!/usr/bin/env python3
"""Generate deterministic synthetic character source images for local-folder assets."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
MANIFESTS_DIR = WORKSPACE_ROOT / "manifests"
DEFAULT_IMAGES_PER_ASSET = 6
DEFAULT_SOURCE_MANIFEST = "configs/characters/spirited_away_sources.internal_rnd.json"
DEFAULT_LOCAL_SOURCE_ROOT = "data/character_refs_sources"


def _timestamp_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_token(raw: str) -> str:
    chars = [c.lower() if c.isalnum() else "_" for c in raw.strip()]
    normalized = "".join(chars).strip("_")
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    return normalized


def _resolve_path(raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return WORKSPACE_ROOT / path


def _to_repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(WORKSPACE_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def _build_alias_map(manifest: dict[str, Any]) -> dict[str, str]:
    alias_map: dict[str, str] = {}
    for entry in manifest.get("characters", []):
        if not isinstance(entry, dict):
            continue
        canonical = _normalize_token(str(entry.get("character_id", "")))
        if not canonical:
            continue

        display_name = _normalize_token(str(entry.get("display_name", canonical)))
        alias_map[canonical] = canonical
        if display_name:
            alias_map[display_name] = canonical

        aliases = entry.get("aliases", [])
        if isinstance(aliases, list):
            for alias in aliases:
                if isinstance(alias, str):
                    normalized_alias = _normalize_token(alias)
                    if normalized_alias:
                        alias_map[normalized_alias] = canonical

    return alias_map


def _resolve_character_id(raw_character: str, alias_map: dict[str, str]) -> str:
    token = _normalize_token(raw_character)
    if not token:
        raise ValueError("asset is missing character")
    return alias_map.get(token, token)


def _resolve_local_folder_path(asset: dict[str, Any], character_id: str, local_source_root: Path) -> Path:
    configured_path = str(asset.get("source_path", "")).strip()
    if configured_path:
        return _resolve_path(configured_path)
    return local_source_root / character_id


def _stable_seed(character_id: str, asset_id: str, index: int) -> int:
    raw = f"{character_id}::{asset_id}::{index}".encode("utf-8")
    digest = hashlib.sha256(raw).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False)


def _target_filename(asset_id: str, index: int, seed: int) -> str:
    token = seed & 0xFFFFFFFF
    return f"{asset_id}__gen{index:02d}__seed{token:08x}.png"


def _deterministic_dimensions(seed: int) -> tuple[int, int]:
    widths = [512, 576, 640, 704]
    heights = [512, 576, 640, 704]
    width = widths[seed % len(widths)]
    height = heights[(seed // len(widths)) % len(heights)]
    return width, height


def _generate_image(character_id: str, asset_id: str, index: int) -> Image.Image:
    seed = _stable_seed(character_id, asset_id, index)
    rng = np.random.default_rng(seed)
    width, height = _deterministic_dimensions(seed)

    yy, xx = np.mgrid[0:height, 0:width]
    phase_x = float(seed % 360) * np.pi / 180.0
    phase_y = float((seed // 11) % 360) * np.pi / 180.0
    phase_z = float((seed // 101) % 360) * np.pi / 180.0

    red = (np.sin((xx / max(width, 1)) * np.pi * 4.0 + phase_x) + 1.0) * 127.5
    green = (np.cos((yy / max(height, 1)) * np.pi * 5.0 + phase_y) + 1.0) * 127.5
    blue = (np.sin(((xx + yy) / max(width + height, 1)) * np.pi * 7.0 + phase_z) + 1.0) * 127.5

    base = np.stack([red, green, blue], axis=-1)
    noise = rng.normal(loc=0.0, scale=17.5, size=(height, width, 3))
    pixels = np.clip(base + noise, 0.0, 255.0).astype(np.uint8)

    image = Image.fromarray(pixels, mode="RGB")
    draw = ImageDraw.Draw(image, mode="RGBA")

    for _ in range(7):
        x0 = int(rng.integers(0, max(width - 80, 1)))
        y0 = int(rng.integers(0, max(height - 80, 1)))
        x1 = x0 + int(rng.integers(36, 180))
        y1 = y0 + int(rng.integers(36, 180))
        rgba = (
            int(rng.integers(30, 226)),
            int(rng.integers(30, 226)),
            int(rng.integers(30, 226)),
            int(rng.integers(80, 176)),
        )
        draw.ellipse([x0, y0, min(x1, width - 1), min(y1, height - 1)], fill=rgba)

    for _ in range(5):
        x0 = int(rng.integers(0, max(width - 100, 1)))
        y0 = int(rng.integers(0, max(height - 100, 1)))
        x1 = x0 + int(rng.integers(50, 220))
        y1 = y0 + int(rng.integers(50, 220))
        rgba = (
            int(rng.integers(30, 226)),
            int(rng.integers(30, 226)),
            int(rng.integers(30, 226)),
            int(rng.integers(65, 145)),
        )
        draw.rectangle([x0, y0, min(x1, width - 1), min(y1, height - 1)], outline=rgba, width=2)

    label = f"{character_id}\n{asset_id}\nseed:{seed & 0xFFFFFFFF:08x}"
    font = ImageFont.load_default()
    draw.rectangle([12, height - 76, width - 12, height - 12], fill=(0, 0, 0, 128))
    draw.multiline_text((18, height - 70), label, fill=(255, 255, 255, 225), font=font, spacing=2)

    return image


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate deterministic synthetic source images for character assets")
    parser.add_argument("--source-manifest", default=DEFAULT_SOURCE_MANIFEST, help="Character source manifest JSON path")
    parser.add_argument(
        "--local-source-root",
        default=DEFAULT_LOCAL_SOURCE_ROOT,
        help="Default local source root used when local_folder assets omit source_path",
    )
    parser.add_argument(
        "--images-per-asset",
        type=int,
        default=DEFAULT_IMAGES_PER_ASSET,
        help="How many deterministic generated images to maintain per local_folder asset",
    )
    parser.add_argument(
        "--refresh-existing",
        action="store_true",
        help="Overwrite expected generated files even if they already exist",
    )
    parser.add_argument(
        "--purge-stale-generated",
        action="store_true",
        help="Delete old generated files that no longer match the expected deterministic set",
    )
    args = parser.parse_args()

    if args.images_per_asset < 1:
        raise ValueError("--images-per-asset must be >= 1")

    source_manifest_path = _resolve_path(args.source_manifest)
    local_source_root = _resolve_path(args.local_source_root)

    manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    alias_map = _build_alias_map(manifest)

    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)

    assets = manifest.get("assets", [])
    if not isinstance(assets, list):
        raise ValueError("source manifest field 'assets' must be a list")

    asset_reports: list[dict[str, Any]] = []

    for asset in sorted([row for row in assets if isinstance(row, dict)], key=lambda row: str(row.get("asset_id", ""))):
        source_kind = str(asset.get("source_kind", "")).strip().lower()
        if source_kind != "local_folder":
            continue

        asset_id = _normalize_token(str(asset.get("asset_id", "")))
        if not asset_id:
            continue

        character_id = _resolve_character_id(str(asset.get("character", "")), alias_map)
        target_folder = _resolve_local_folder_path(asset, character_id, local_source_root)
        target_folder.mkdir(parents=True, exist_ok=True)

        expected_paths: list[Path] = []
        created = 0
        refreshed = 0
        reused = 0

        for index in range(1, args.images_per_asset + 1):
            seed = _stable_seed(character_id, asset_id, index)
            filename = _target_filename(asset_id, index, seed)
            out_path = target_folder / filename
            expected_paths.append(out_path)

            existed_before = out_path.exists()

            if existed_before and not args.refresh_existing:
                reused += 1
                continue

            image = _generate_image(character_id, asset_id, index)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            image.save(out_path, format="PNG", optimize=False, compress_level=6)
            if existed_before:
                refreshed += 1
            else:
                created += 1

        removed = 0
        if args.purge_stale_generated:
            expected_names = {path.name for path in expected_paths}
            for candidate in target_folder.glob(f"{asset_id}__gen*"):
                if not candidate.is_file() or candidate.suffix.lower() != ".png":
                    continue
                if candidate.name in expected_names:
                    continue
                candidate.unlink()
                removed += 1

        asset_reports.append(
            {
                "asset_id": asset_id,
                "character_id": character_id,
                "source_folder": _to_repo_relative(target_folder),
                "images_per_asset": args.images_per_asset,
                "created": created,
                "refreshed": refreshed,
                "reused": reused,
                "removed_stale": removed,
                "expected_files": [_to_repo_relative(path) for path in expected_paths],
            }
        )

    summary = {
        "assets_processed": len(asset_reports),
        "images_created": sum(int(row["created"]) for row in asset_reports),
        "images_refreshed": sum(int(row["refreshed"]) for row in asset_reports),
        "images_reused": sum(int(row["reused"]) for row in asset_reports),
        "images_removed_stale": sum(int(row["removed_stale"]) for row in asset_reports),
    }

    payload = {
        "version": "1.0.0",
        "generated_at": _now_iso(),
        "source_manifest": _to_repo_relative(source_manifest_path),
        "local_source_root": _to_repo_relative(local_source_root),
        "images_per_asset": int(args.images_per_asset),
        "refresh_existing": bool(args.refresh_existing),
        "purge_stale_generated": bool(args.purge_stale_generated),
        "summary": summary,
        "assets": asset_reports,
    }

    timestamped_report = MANIFESTS_DIR / f"character_source_generation.{_timestamp_utc()}.json"
    latest_report = MANIFESTS_DIR / "character_source_generation.latest.json"
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    timestamped_report.write_text(text, encoding="utf-8")
    latest_report.write_text(text, encoding="utf-8")

    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

