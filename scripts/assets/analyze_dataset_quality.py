#!/usr/bin/env python3
"""Compute objective quality indicators for an image folder."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

try:
    from scripts.assets.image_quality import SUPPORTED_EXTENSIONS, analyze_and_score, quality_sort_key
except ModuleNotFoundError:
    from image_quality import SUPPORTED_EXTENSIONS, analyze_and_score, quality_sort_key


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze objective image quality metrics")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output", required=False)
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    files = sorted(
        [
            path
            for path in input_dir.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
        ]
    )
    rows = [analyze_and_score(path) for path in files]

    ranked = sorted(rows, key=quality_sort_key)
    for rank, row in enumerate(ranked, start=1):
        row["quality_rank"] = rank

    rows_by_sharp = sorted(rows, key=lambda row: row["sharpness_laplacian_var"])
    rows_by_quality = sorted(rows, key=quality_sort_key)

    summary = {
        "count": len(rows),
        "min_pixels": min((row["pixels"] for row in rows), default=0),
        "max_pixels": max((row["pixels"] for row in rows), default=0),
        "avg_brightness": float(np.mean([row["brightness_mean"] for row in rows])) if rows else 0.0,
        "avg_contrast": float(np.mean([row["contrast_std"] for row in rows])) if rows else 0.0,
        "avg_sharpness": float(np.mean([row["sharpness_laplacian_var"] for row in rows])) if rows else 0.0,
        "avg_quality_score": float(np.mean([row["quality_score"] for row in rows])) if rows else 0.0,
        "lowest_sharpness_files": [row["file"] for row in rows_by_sharp[:5]],
        "highest_sharpness_files": [row["file"] for row in rows_by_sharp[-5:]],
        "top_quality_files": [row["file"] for row in rows_by_quality[:5]],
        "lowest_quality_files": [row["file"] for row in rows_by_quality[-5:]],
    }

    payload = {"summary": summary, "files": sorted(rows, key=lambda row: row["quality_rank"])}
    text = json.dumps(payload, ensure_ascii=False, indent=2)

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")

    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

