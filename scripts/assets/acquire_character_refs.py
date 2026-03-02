#!/usr/bin/env python3
"""Acquire character references from configured sources for INTERNAL_RND."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

try:
    from scripts.assets.image_quality import SUPPORTED_EXTENSIONS, analyze_and_score, quality_sort_key
except ModuleNotFoundError:
    from image_quality import SUPPORTED_EXTENSIONS, analyze_and_score, quality_sort_key


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
MANIFESTS_DIR = WORKSPACE_ROOT / "manifests"
DEFAULT_FILE_PATTERNS = sorted(f"*{ext}" for ext in SUPPORTED_EXTENSIONS)


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


def download_file(url: str, out_path: Path, timeout: int = 120) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=timeout) as response:
        response.raise_for_status()
        with out_path.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)


def _matches_patterns(filename: str, patterns: list[str]) -> bool:
    lowered = filename.lower()
    return any(fnmatch.fnmatch(lowered, pattern.lower()) for pattern in patterns)


def _build_character_registry(
    manifest: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    registry: dict[str, dict[str, Any]] = {}
    alias_map: dict[str, str] = {}

    for entry in manifest.get("characters", []):
        if not isinstance(entry, dict):
            continue
        canonical = _normalize_token(str(entry.get("character_id", "")))
        if not canonical:
            continue

        display_name = str(entry.get("display_name", canonical))
        aliases = [a for a in entry.get("aliases", []) if isinstance(a, str)]

        registry[canonical] = {
            "character_id": canonical,
            "display_name": display_name,
            "aliases": aliases,
        }

        alias_map[canonical] = canonical
        alias_map[_normalize_token(display_name)] = canonical
        for alias in aliases:
            normalized_alias = _normalize_token(alias)
            if normalized_alias:
                alias_map[normalized_alias] = canonical

    for asset in manifest.get("assets", []):
        if not isinstance(asset, dict):
            continue
        raw_character = str(asset.get("character", ""))
        canonical = _normalize_token(raw_character)
        if not canonical:
            continue
        if canonical not in registry:
            registry[canonical] = {
                "character_id": canonical,
                "display_name": canonical.replace("_", " ").title(),
                "aliases": [],
            }
        alias_map[canonical] = canonical

    return registry, alias_map


def _resolve_character_id(raw_character: str, alias_map: dict[str, str]) -> str:
    token = _normalize_token(raw_character)
    if not token:
        raise ValueError("missing character")
    return alias_map.get(token, token)


def _collect_asset_sources(
    asset: dict[str, Any],
    character_id: str,
    local_source_root: Path,
) -> list[dict[str, Any]]:
    kind = str(asset.get("source_kind", "")).strip().lower()
    sources: list[dict[str, Any]] = []

    if kind in {"manual_upload", "local_file"}:
        source_path = str(asset.get("source_path", "")).strip()
        if source_path:
            path = _resolve_path(source_path)
            sources.append(
                {
                    "source_type": "local_file",
                    "source_ref": str(path),
                    "path": path,
                }
            )
        return sources

    if kind == "local_folder":
        configured_path = str(asset.get("source_path", "")).strip()
        folder_path = _resolve_path(configured_path) if configured_path else local_source_root / character_id
        fallback_path = local_source_root / character_id
        if not folder_path.exists() and fallback_path.exists():
            folder_path = fallback_path

        if not folder_path.exists() or not folder_path.is_dir():
            raise FileNotFoundError(f"source folder not found: {folder_path}")

        recursive = bool(asset.get("recursive", True))
        raw_patterns = asset.get("file_patterns", [])
        patterns = [p for p in raw_patterns if isinstance(p, str) and p.strip()] or DEFAULT_FILE_PATTERNS

        candidates = folder_path.rglob("*") if recursive else folder_path.glob("*")
        files = [
            path
            for path in candidates
            if path.is_file()
            and path.suffix.lower() in SUPPORTED_EXTENSIONS
            and _matches_patterns(path.name, patterns)
        ]
        for path in sorted(files, key=lambda p: p.as_posix().lower()):
            sources.append(
                {
                    "source_type": "local_file",
                    "source_ref": str(path),
                    "path": path,
                }
            )
        return sources

    if kind in {"licensed_url", "url"}:
        urls: list[str] = []
        source_url = str(asset.get("source_url", "")).strip()
        if source_url:
            urls.append(source_url)
        source_urls = asset.get("source_urls", [])
        if isinstance(source_urls, list):
            urls.extend([str(u).strip() for u in source_urls if isinstance(u, str) and str(u).strip()])

        for url in sorted(set(urls)):
            sources.append(
                {
                    "source_type": "url",
                    "source_ref": url,
                    "url": url,
                }
            )
        return sources

    raise ValueError(f"unsupported source_kind '{kind}'")


def _load_state(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}

    payload = json.loads(path.read_text(encoding="utf-8"))
    records_by_sha: dict[str, dict[str, Any]] = {}

    for entry in payload.get("records", []):
        if not isinstance(entry, dict):
            continue
        sha = str(entry.get("sha256", "")).strip().lower()
        if not sha:
            continue

        path_raw = str(entry.get("path", "")).strip()
        if not path_raw:
            continue
        file_path = _resolve_path(path_raw)
        if not file_path.exists() or not file_path.is_file():
            continue

        character_id = _normalize_token(str(entry.get("character_id", "")))
        if not character_id:
            continue

        ext = str(entry.get("ext", "")).strip().lower() or file_path.suffix.lower() or ".jpg"
        analyzed = analyze_and_score(file_path)

        records_by_sha[sha] = {
            "sha256": sha,
            "character_id": character_id,
            "ext": ext,
            "path": file_path,
            "staged_path": None,
            "source_asset_ids": set(
                [a for a in entry.get("source_asset_ids", []) if isinstance(a, str) and a.strip()]
            ),
            "source_refs": set([r for r in entry.get("source_refs", []) if isinstance(r, str) and r.strip()]),
            **analyzed,
        }

    return records_by_sha


def _save_state(path: Path, records_by_sha: dict[str, dict[str, Any]]) -> None:
    ordered = sorted(
        records_by_sha.values(),
        key=lambda record: (
            str(record.get("character_id", "")),
            int(record.get("quality_rank", 10_000)),
            str(record.get("sha256", "")),
        ),
    )

    payload = {
        "version": "1.0.0",
        "generated_at": _now_iso(),
        "records": [
            {
                "sha256": str(record["sha256"]),
                "character_id": str(record["character_id"]),
                "path": _to_repo_relative(Path(record["path"])),
                "ext": str(record["ext"]),
                "quality_score": float(record["quality_score"]),
                "quality_bucket": int(record["quality_bucket"]),
                "quality_rank": int(record.get("quality_rank", 0)),
                "source_asset_ids": sorted(record["source_asset_ids"]),
                "source_refs": sorted(record["source_refs"]),
            }
            for record in ordered
        ],
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _assign_quality_ranks(records_by_sha: dict[str, dict[str, Any]], output_dir: Path) -> None:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records_by_sha.values():
        grouped[str(record["character_id"])].append(record)

    for character_id, records in grouped.items():
        ordered = sorted(records, key=quality_sort_key)
        for rank, record in enumerate(ordered, start=1):
            bucket = int(record["quality_bucket"])
            ext = str(record["ext"] or ".jpg").lower()
            record["quality_rank"] = rank
            record["target_path"] = output_dir / f"{character_id}__score{bucket:02d}__rank{rank:02d}{ext}"


def _materialize_ranked_files(
    records_by_sha: dict[str, dict[str, Any]],
    temp_dir: Path,
) -> list[dict[str, Any]]:
    ordered = sorted(
        records_by_sha.values(),
        key=lambda record: (
            str(record["character_id"]),
            int(record.get("quality_rank", 10_000)),
            str(record["sha256"]),
        ),
    )

    safe_sources: dict[str, Path] = {}
    for record in ordered:
        source_path = Path(record["staged_path"] or record["path"])
        target_path = Path(record["target_path"])
        if record["staged_path"] is None and source_path != target_path:
            temp_copy = temp_dir / f"existing_{record['sha256']}{source_path.suffix.lower() or '.jpg'}"
            temp_copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, temp_copy)
            safe_sources[str(record["sha256"])] = temp_copy
        else:
            safe_sources[str(record["sha256"])] = source_path

    target_paths = {Path(record["target_path"]).resolve() for record in ordered}

    for record in ordered:
        source_path = safe_sources[str(record["sha256"])]
        target_path = Path(record["target_path"])
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if source_path.resolve() != target_path.resolve():
            shutil.copy2(source_path, target_path)

    file_transforms: list[dict[str, Any]] = []

    for record in ordered:
        previous = Path(record["path"])
        target = Path(record["target_path"])
        was_staged = record["staged_path"] is not None
        previous_resolved = previous.resolve() if previous.exists() else previous
        target_resolved = target.resolve()

        if was_staged:
            action = "created"
        elif previous_resolved == target_resolved:
            action = "unchanged"
        else:
            action = "renamed"

        if previous.exists() and previous.resolve() != target.resolve():
            if previous_resolved not in target_paths and previous.is_file():
                previous.unlink()

        file_transforms.append(
            {
                "character_id": str(record["character_id"]),
                "sha256": str(record["sha256"]),
                "action": action,
                "from": None if was_staged else _to_repo_relative(previous),
                "to": _to_repo_relative(target),
                "quality_rank": int(record.get("quality_rank", 0)),
                "quality_bucket": int(record["quality_bucket"]),
                "quality_score": float(record["quality_score"]),
            }
        )

        record["path"] = target
        record["staged_path"] = None

    return file_transforms


def _compute_quality_report(records_by_sha: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows = sorted(
        records_by_sha.values(),
        key=lambda record: (
            str(record["character_id"]),
            int(record.get("quality_rank", 10_000)),
            str(record["sha256"]),
        ),
    )
    report_rows = [
        {
            "character_id": str(record["character_id"]),
            "file": Path(record["path"]).name,
            "path": _to_repo_relative(Path(record["path"])),
            "sha256": str(record["sha256"]),
            "quality_rank": int(record.get("quality_rank", 0)),
            "quality_bucket": int(record["quality_bucket"]),
            "quality_score": float(record["quality_score"]),
            "pixels": int(record["pixels"]),
            "sharpness_laplacian_var": float(record["sharpness_laplacian_var"]),
            "contrast_std": float(record["contrast_std"]),
            "dark_clip_pct": float(record["dark_clip_pct"]),
            "bright_clip_pct": float(record["bright_clip_pct"]),
            "composition_rule_of_thirds_score": float(record["composition_rule_of_thirds_score"]),
        }
        for record in rows
    ]

    quality_values = [float(record["quality_score"]) for record in rows]
    summary = {
        "count": len(rows),
        "avg_quality_score": (sum(quality_values) / len(quality_values)) if quality_values else 0.0,
        "best_files": [
            {
                "file": row["file"],
                "character_id": row["character_id"],
                "quality_score": row["quality_score"],
            }
            for row in sorted(report_rows, key=lambda row: -float(row["quality_score"]))[:5]
        ],
        "worst_files": [
            {
                "file": row["file"],
                "character_id": row["character_id"],
                "quality_score": row["quality_score"],
            }
            for row in sorted(report_rows, key=lambda row: float(row["quality_score"]))[:5]
        ],
    }
    return {"summary": summary, "files": report_rows}


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
    parser.add_argument(
        "--local-source-root",
        default="data/character_refs_sources",
        help="Default local root folder used by local_folder assets",
    )
    parser.add_argument(
        "--quality-report",
        default="manifests/image_quality_report.json",
        help="Quality report output path",
    )
    parser.add_argument(
        "--state-file",
        default=None,
        help="Optional acquisition state file path (default: <output-dir>/.character_refs_state.json)",
    )
    parser.add_argument(
        "--request-timeout",
        default=120,
        type=int,
        help="HTTP timeout in seconds for URL sources",
    )
    parser.add_argument(
        "--fail-on-missing-assets",
        action="store_true",
        help="Exit non-zero if any configured asset has no resolved sources",
    )
    args = parser.parse_args()

    source_manifest_path = _resolve_path(args.source_manifest)
    output_dir = _resolve_path(args.output_dir)
    local_source_root = _resolve_path(args.local_source_root)
    quality_report_path = _resolve_path(args.quality_report)
    state_file_path = _resolve_path(args.state_file) if args.state_file else output_dir / ".character_refs_state.json"

    manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    registry, alias_map = _build_character_registry(manifest)

    output_dir.mkdir(parents=True, exist_ok=True)
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)

    temp_dir = output_dir / ".acquire_tmp" / _timestamp_utc()
    temp_dir.mkdir(parents=True, exist_ok=True)

    records_by_sha = _load_state(state_file_path)
    asset_results: list[dict[str, Any]] = []
    runtime_errors: list[str] = []
    runtime_warnings: list[str] = []

    try:
        assets = manifest.get("assets", [])
        if not isinstance(assets, list):
            raise ValueError("source manifest field 'assets' must be a list")

        for asset in sorted(
            [asset for asset in assets if isinstance(asset, dict)],
            key=lambda item: str(item.get("asset_id", "")),
        ):
            asset_id = str(asset.get("asset_id", "")).strip()
            raw_character = str(asset.get("character", "")).strip()
            source_kind = str(asset.get("source_kind", "")).strip()

            result = {
                "asset_id": asset_id,
                "character": raw_character,
                "source_kind": source_kind,
                "status": "failed",
                "resolved": [],
                "duplicates": [],
                "errors": [],
            }

            if not asset_id:
                result["errors"].append("missing asset_id")
                asset_results.append(result)
                continue

            try:
                character_id = _resolve_character_id(raw_character, alias_map)
                if character_id not in registry:
                    raise ValueError(f"character '{raw_character}' is not defined")
            except Exception as exc:  # noqa: BLE001
                result["errors"].append(str(exc))
                asset_results.append(result)
                continue

            try:
                sources = _collect_asset_sources(asset, character_id, local_source_root)
            except Exception as exc:  # noqa: BLE001
                result["errors"].append(str(exc))
                asset_results.append(result)
                continue

            if not sources:
                result["errors"].append("no source files/URLs discovered")
                asset_results.append(result)
                continue

            for idx, source in enumerate(sources, start=1):
                try:
                    source_type = str(source["source_type"])
                    source_ref = str(source["source_ref"])

                    if source_type == "local_file":
                        source_path = Path(source["path"])
                        if not source_path.exists() or not source_path.is_file():
                            raise FileNotFoundError(f"source file not found: {source_path}")
                        suffix = source_path.suffix.lower()
                        if suffix not in SUPPORTED_EXTENSIONS:
                            raise ValueError(f"unsupported extension '{suffix}' from source file: {source_path}")
                        staged_path = temp_dir / f"{asset_id}__{idx:04d}{suffix}"
                        staged_path.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source_path, staged_path)
                    elif source_type == "url":
                        source_url = str(source["url"])
                        suffix = Path(urlparse(source_url).path).suffix.lower() or ".jpg"
                        if suffix not in SUPPORTED_EXTENSIONS:
                            suffix = ".jpg"
                        staged_path = temp_dir / f"{asset_id}__{idx:04d}{suffix}"
                        download_file(source_url, staged_path, timeout=args.request_timeout)
                    else:
                        raise ValueError(f"unsupported source type '{source_type}'")

                    sha = sha256_file(staged_path)
                    if sha in records_by_sha:
                        existing = records_by_sha[sha]
                        existing["source_asset_ids"].add(asset_id)
                        existing["source_refs"].add(source_ref)

                        if str(existing["character_id"]) != character_id:
                            runtime_warnings.append(
                                f"{asset_id}: duplicate hash {sha} already mapped to '{existing['character_id']}', "
                                f"skipping character remap from '{character_id}'"
                            )

                        result["duplicates"].append(
                            {
                                "source_ref": source_ref,
                                "sha256": sha,
                                "existing_path": _to_repo_relative(Path(existing["path"])),
                            }
                        )
                        continue

                    analyzed = analyze_and_score(staged_path)
                    records_by_sha[sha] = {
                        "sha256": sha,
                        "character_id": character_id,
                        "ext": staged_path.suffix.lower() or ".jpg",
                        "path": staged_path,
                        "staged_path": staged_path,
                        "source_asset_ids": {asset_id},
                        "source_refs": {source_ref},
                        **analyzed,
                    }

                    result["resolved"].append(
                        {
                            "source_ref": source_ref,
                            "sha256": sha,
                            "quality_score": float(analyzed["quality_score"]),
                            "quality_bucket": int(analyzed["quality_bucket"]),
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    result["errors"].append(f"{source.get('source_ref', 'unknown')}: {exc}")

            resolved_count = len(result["resolved"]) + len(result["duplicates"])
            if resolved_count > 0 and not result["errors"]:
                result["status"] = "success"
            elif resolved_count > 0 and result["errors"]:
                result["status"] = "partial"
            else:
                result["status"] = "failed"

            asset_results.append(result)

        _assign_quality_ranks(records_by_sha, output_dir)
        file_transforms = _materialize_ranked_files(records_by_sha, temp_dir=temp_dir)
        _save_state(state_file_path, records_by_sha)

        quality_payload = _compute_quality_report(records_by_sha)
        quality_report_path.parent.mkdir(parents=True, exist_ok=True)
        quality_report_path.write_text(
            json.dumps(quality_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        rank_lookup = {
            str(sha): {
                "character_id": str(record["character_id"]),
                "path": _to_repo_relative(Path(record["path"])),
                "quality_rank": int(record.get("quality_rank", 0)),
                "quality_bucket": int(record["quality_bucket"]),
                "quality_score": float(record["quality_score"]),
            }
            for sha, record in records_by_sha.items()
        }

        for asset_result in asset_results:
            for section in ["resolved", "duplicates"]:
                for ref in asset_result[section]:
                    sha = str(ref.get("sha256", ""))
                    if sha in rank_lookup:
                        ref.update(rank_lookup[sha])

        acquired = sorted(
            [
                {
                    "character_id": str(record["character_id"]),
                    "path": _to_repo_relative(Path(record["path"])),
                    "sha256": str(record["sha256"]),
                    "quality_rank": int(record.get("quality_rank", 0)),
                    "quality_bucket": int(record["quality_bucket"]),
                    "quality_score": float(record["quality_score"]),
                    "source_asset_ids": sorted(record["source_asset_ids"]),
                    "source_refs": sorted(record["source_refs"]),
                }
                for record in records_by_sha.values()
            ],
            key=lambda row: (
                str(row["character_id"]),
                int(row["quality_rank"]),
                str(row["sha256"]),
            ),
        )

        failed_assets = [result for result in asset_results if result["status"] == "failed"]
        partial_assets = [result for result in asset_results if result["status"] == "partial"]

        fatal_error = bool(runtime_errors) or (args.fail_on_missing_assets and bool(failed_assets))
        report = {
            "valid": not fatal_error,
            "source_manifest": _to_repo_relative(source_manifest_path),
            "output_dir": _to_repo_relative(output_dir),
            "local_source_root": _to_repo_relative(local_source_root),
            "state_file": _to_repo_relative(state_file_path),
            "quality_report": _to_repo_relative(quality_report_path),
            "summary": {
                "assets_configured": len(asset_results),
                "assets_success": len([result for result in asset_results if result["status"] == "success"]),
                "assets_partial": len(partial_assets),
                "assets_failed": len(failed_assets),
                "refs_total": len(acquired),
            },
            "characters": sorted(registry.values(), key=lambda row: str(row["character_id"])),
            "acquired": acquired,
            "file_transforms": sorted(
                file_transforms,
                key=lambda row: (
                    str(row["character_id"]),
                    int(row["quality_rank"]),
                    str(row["sha256"]),
                ),
            ),
            "asset_results": asset_results,
            "warnings": runtime_warnings,
            "errors": runtime_errors,
            "generated_at": _now_iso(),
        }

        out_path = MANIFESTS_DIR / f"character_assets_acquisition.{_timestamp_utc()}.json"
        latest_path = MANIFESTS_DIR / "character_assets_acquisition.latest.json"
        text = json.dumps(report, ensure_ascii=False, indent=2)
        out_path.write_text(text, encoding="utf-8")
        latest_path.write_text(text, encoding="utf-8")

        print(text)
        return 1 if fatal_error else 0
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())

