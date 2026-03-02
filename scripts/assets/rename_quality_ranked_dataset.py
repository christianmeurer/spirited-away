#!/usr/bin/env python3
"""Quality-score and deterministically rename dataset images with rank/score tokens."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from scripts.assets.image_quality import SUPPORTED_EXTENSIONS, analyze_and_score, quality_sort_key
except ModuleNotFoundError:
    from image_quality import SUPPORTED_EXTENSIONS, analyze_and_score, quality_sort_key


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
MANIFESTS_DIR = WORKSPACE_ROOT / "manifests"
DEFAULT_SOURCE_MANIFEST = "configs/characters/spirited_away_sources.internal_rnd.json"
NORMALIZED_STEM_RE = re.compile(r"^(?P<prefix>[a-z0-9_]+)__score(?P<bucket>\d{2})__rank(?P<rank>\d+)$")


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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_character_ids(source_manifest_path: Path) -> set[str]:
    payload = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    character_ids: set[str] = set()
    characters = payload.get("characters", [])
    if not isinstance(characters, list):
        return character_ids

    for row in characters:
        if not isinstance(row, dict):
            continue
        token = _normalize_token(str(row.get("character_id", "")))
        if token:
            character_ids.add(token)
    return character_ids


def _discover_files(input_dir: Path, recursive: bool) -> list[Path]:
    candidates = input_dir.rglob("*") if recursive else input_dir.glob("*")
    files = [path for path in candidates if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS]
    return sorted(files, key=lambda path: path.as_posix().lower())


def _extract_character_group(path: Path, default_prefix: str, character_ids: set[str]) -> str:
    name = path.stem
    for character_id in sorted(character_ids, key=len, reverse=True):
        if name == character_id or name.startswith(f"{character_id}__"):
            return character_id

    normalized_name = _normalize_token(name)
    if "__" in normalized_name:
        candidate = normalized_name.split("__", maxsplit=1)[0]
    else:
        candidate = normalized_name
    return candidate if candidate in character_ids else default_prefix


def _build_target_name(prefix: str, quality_bucket: int, rank: int, rank_width: int, ext: str) -> str:
    return f"{prefix}__score{quality_bucket:02d}__rank{rank:0{rank_width}d}{ext.lower()}"


def _is_already_normalized(path: Path, prefix: str, quality_bucket: int, rank: int, rank_width: int) -> bool:
    expected_stem = f"{prefix}__score{quality_bucket:02d}__rank{rank:0{rank_width}d}"
    match = NORMALIZED_STEM_RE.match(path.stem)
    if not match:
        return False
    return path.stem == expected_stem


def _rename_two_phase(records: list[dict[str, Any]]) -> None:
    pending = [record for record in records if str(record["action"]) == "renamed"]
    temp_paths_in_use: set[Path] = set()

    for index, record in enumerate(pending, start=1):
        source_path = Path(record["source_path"])
        tmp_path = source_path.with_name(f".__rank_tmp__{index:06d}__{record['sha256'][:10]}{source_path.suffix.lower()}")
        while tmp_path.exists() or tmp_path in temp_paths_in_use:
            tmp_path = source_path.with_name(
                f".__rank_tmp__{index:06d}__{record['sha256'][:12]}_{len(temp_paths_in_use):03d}{source_path.suffix.lower()}"
            )

        source_path.rename(tmp_path)
        record["temp_path"] = tmp_path
        temp_paths_in_use.add(tmp_path)

    for record in sorted(pending, key=lambda row: str(Path(row["target_path"]).as_posix()).lower()):
        temp_path = Path(record["temp_path"])
        target_path = Path(record["target_path"])
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.rename(target_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Quality-rank and deterministically rename images in a dataset folder")
    parser.add_argument("--input-dir", required=True, help="Dataset directory containing images")
    parser.add_argument(
        "--group-mode",
        default="dataset",
        choices=["dataset", "character_from_filename"],
        help="How to build naming prefixes for ranking groups",
    )
    parser.add_argument(
        "--name-prefix",
        default=None,
        help="Prefix for dataset mode naming (default: normalized input directory name)",
    )
    parser.add_argument(
        "--source-manifest",
        default=DEFAULT_SOURCE_MANIFEST,
        help="Character source manifest (used for character_from_filename mode)",
    )
    parser.add_argument("--recursive", action="store_true", help="Recursively include images from subfolders")
    parser.add_argument("--dry-run", action="store_true", help="Only compute mappings, do not rename files")
    args = parser.parse_args()

    input_dir = _resolve_path(args.input_dir)
    if not input_dir.exists() or not input_dir.is_dir():
        raise FileNotFoundError(f"input directory not found: {input_dir}")

    name_prefix = _normalize_token(args.name_prefix or input_dir.name)
    if not name_prefix:
        raise ValueError("unable to derive non-empty name prefix")

    character_ids: set[str] = set()
    if args.group_mode == "character_from_filename":
        source_manifest = _resolve_path(args.source_manifest)
        character_ids = _load_character_ids(source_manifest)

    files = _discover_files(input_dir, recursive=bool(args.recursive))
    rows: list[dict[str, Any]] = []

    for path in files:
        analyzed = analyze_and_score(path)
        group_key = (
            _extract_character_group(path, default_prefix=name_prefix, character_ids=character_ids)
            if args.group_mode == "character_from_filename"
            else name_prefix
        )
        rows.append(
            {
                "source_path": path,
                "source_name": path.name,
                "group_key": group_key,
                "sha256": sha256_file(path),
                "ext": path.suffix,
                **analyzed,
            }
        )

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["group_key"])].append(row)

    for group_key, group_rows in grouped.items():
        ranked = sorted(group_rows, key=quality_sort_key)
        rank_width = max(2, len(str(len(ranked))))
        for rank, row in enumerate(ranked, start=1):
            bucket = int(row["quality_bucket"])
            target_name = _build_target_name(
                prefix=group_key,
                quality_bucket=bucket,
                rank=rank,
                rank_width=rank_width,
                ext=str(row["ext"]),
            )
            target_path = input_dir / target_name
            normalized_match = _is_already_normalized(
                path=Path(row["source_path"]),
                prefix=group_key,
                quality_bucket=bucket,
                rank=rank,
                rank_width=rank_width,
            )
            unchanged = Path(row["source_path"]).name == target_name or normalized_match
            row["quality_rank"] = rank
            row["target_name"] = target_name
            row["target_path"] = target_path
            row["action"] = "unchanged" if unchanged else "renamed"

    ordered = sorted(
        rows,
        key=lambda row: (
            str(row["group_key"]),
            int(row.get("quality_rank", 10_000)),
            str(row["sha256"]),
        ),
    )

    if not args.dry_run:
        _rename_two_phase(ordered)

    transforms = [
        {
            "group_key": str(row["group_key"]),
            "action": str(row["action"]),
            "old_path": _to_repo_relative(Path(row["source_path"])),
            "new_path": _to_repo_relative(Path(row["target_path"])),
            "quality_rank": int(row.get("quality_rank", 0)),
            "quality_bucket": int(row["quality_bucket"]),
            "quality_score": float(row["quality_score"]),
            "sha256": str(row["sha256"]),
        }
        for row in ordered
    ]

    summary = {
        "count": len(transforms),
        "renamed": len([row for row in transforms if row["action"] == "renamed"]),
        "unchanged": len([row for row in transforms if row["action"] == "unchanged"]),
    }

    payload = {
        "version": "1.0.0",
        "generated_at": _now_iso(),
        "input_dir": _to_repo_relative(input_dir),
        "group_mode": args.group_mode,
        "name_prefix": name_prefix,
        "recursive": bool(args.recursive),
        "dry_run": bool(args.dry_run),
        "summary": summary,
        "files": transforms,
    }

    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)
    dataset_token = _normalize_token(input_dir.name) or "dataset"
    report_timestamped = MANIFESTS_DIR / f"quality_ranked_rename.{dataset_token}.{_timestamp_utc()}.json"
    report_latest = MANIFESTS_DIR / f"quality_ranked_rename.{dataset_token}.latest.json"
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    report_timestamped.write_text(text, encoding="utf-8")
    report_latest.write_text(text, encoding="utf-8")

    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

