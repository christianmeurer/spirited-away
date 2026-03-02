#!/usr/bin/env python3
"""Run deterministic character-source generation, acquisition, and quality-ranked renaming."""

from __future__ import annotations

import argparse
import subprocess
import sys


def _run_cmd(cmd: list[str]) -> None:
    print("Executing:", " ".join(cmd))
    completed = subprocess.run(cmd, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"Command failed ({completed.returncode}): {' '.join(cmd)}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate deterministic local character sources and apply quality-ranked renaming"
    )
    parser.add_argument(
        "--source-manifest",
        default="configs/characters/spirited_away_sources.internal_rnd.json",
        help="Character source config used by generation and acquisition",
    )
    parser.add_argument(
        "--local-source-root",
        default="data/character_refs_sources",
        help="Local source root for generated character images",
    )
    parser.add_argument(
        "--character-output-dir",
        default="data/character_refs",
        help="Character references output directory",
    )
    parser.add_argument(
        "--fotos-dir",
        default="data/Fotos-Aurora",
        help="Fotos-Aurora dataset directory",
    )
    parser.add_argument(
        "--images-per-asset",
        type=int,
        default=6,
        help="Deterministic generated images to maintain per local_folder asset",
    )
    parser.add_argument(
        "--refresh-generated-sources",
        action="store_true",
        help="Overwrite expected generated source files",
    )
    parser.add_argument(
        "--purge-stale-generated-sources",
        action="store_true",
        help="Delete old generated source files not in expected deterministic set",
    )
    parser.add_argument(
        "--request-timeout",
        type=int,
        default=120,
        help="HTTP timeout in seconds for acquisition URL sources",
    )
    parser.add_argument(
        "--fail-on-missing-assets",
        action="store_true",
        help="Fail if any configured acquisition asset resolves to no source",
    )
    args = parser.parse_args()

    python_exec = sys.executable

    generation_cmd = [
        python_exec,
        "scripts/assets/generate_character_source_images.py",
        "--source-manifest",
        args.source_manifest,
        "--local-source-root",
        args.local_source_root,
        "--images-per-asset",
        str(args.images_per_asset),
    ]
    if args.refresh_generated_sources:
        generation_cmd.append("--refresh-existing")
    if args.purge_stale_generated_sources:
        generation_cmd.append("--purge-stale-generated")
    _run_cmd(generation_cmd)

    acquire_cmd = [
        python_exec,
        "scripts/assets/acquire_character_refs.py",
        "--source-manifest",
        args.source_manifest,
        "--output-dir",
        args.character_output_dir,
        "--local-source-root",
        args.local_source_root,
        "--quality-report",
        "manifests/image_quality_report.json",
        "--request-timeout",
        str(args.request_timeout),
    ]
    if args.fail_on_missing_assets:
        acquire_cmd.append("--fail-on-missing-assets")
    _run_cmd(acquire_cmd)

    _run_cmd(
        [
            python_exec,
            "scripts/assets/rename_quality_ranked_dataset.py",
            "--input-dir",
            args.fotos_dir,
            "--group-mode",
            "dataset",
            "--name-prefix",
            "fotos_aurora",
        ]
    )

    _run_cmd(
        [
            python_exec,
            "scripts/assets/rename_quality_ranked_dataset.py",
            "--input-dir",
            args.character_output_dir,
            "--group-mode",
            "character_from_filename",
            "--source-manifest",
            args.source_manifest,
        ]
    )

    print("Character asset generation and deterministic quality-ranked renaming completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

