#!/usr/bin/env python3
"""Compute objective quality indicators for an image folder."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image


SUPPORTED = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}


def laplacian_variance(gray: np.ndarray) -> float:
    core = gray[1:-1, 1:-1]
    lap = (
        gray[:-2, 1:-1]
        + gray[2:, 1:-1]
        + gray[1:-1, :-2]
        + gray[1:-1, 2:]
        - 4.0 * core
    )
    return float(np.var(np.abs(lap)))


def analyze_image(path: Path) -> dict:
    with Image.open(path) as img:
        gray = np.asarray(img.convert("L"), dtype=np.float32)

    h, w = gray.shape
    return {
        "file": path.name,
        "width": int(w),
        "height": int(h),
        "pixels": int(w * h),
        "brightness_mean": float(gray.mean()),
        "contrast_std": float(gray.std()),
        "sharpness_laplacian_var": laplacian_variance(gray),
        "dark_clip_pct": float((gray < 25).mean() * 100.0),
        "bright_clip_pct": float((gray > 245).mean() * 100.0),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze objective image quality metrics")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output", required=False)
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    files = sorted([p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED])
    rows = [analyze_image(p) for p in files]

    rows_by_sharp = sorted(rows, key=lambda r: r["sharpness_laplacian_var"])
    summary = {
        "count": len(rows),
        "min_pixels": min((r["pixels"] for r in rows), default=0),
        "max_pixels": max((r["pixels"] for r in rows), default=0),
        "avg_brightness": float(np.mean([r["brightness_mean"] for r in rows])) if rows else 0.0,
        "avg_contrast": float(np.mean([r["contrast_std"] for r in rows])) if rows else 0.0,
        "avg_sharpness": float(np.mean([r["sharpness_laplacian_var"] for r in rows])) if rows else 0.0,
        "lowest_sharpness_files": [r["file"] for r in rows_by_sharp[:5]],
        "highest_sharpness_files": [r["file"] for r in rows_by_sharp[-5:]],
    }

    payload = {"summary": summary, "files": rows}
    text = json.dumps(payload, ensure_ascii=False, indent=2)

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")

    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

