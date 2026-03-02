#!/usr/bin/env python3
"""Shared image-quality metrics and scoring helpers for reference curation."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


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


def composition_rule_of_thirds_score(gray: np.ndarray) -> float:
    """Proxy for composition usefulness via detail near thirds intersections."""
    height, width = gray.shape
    if height < 8 or width < 8:
        return 0.0

    gy, gx = np.gradient(gray)
    grad = np.sqrt((gx * gx) + (gy * gy))
    global_energy = float(np.mean(grad))
    if global_energy <= 1e-9:
        return 0.0

    ys = [int(round(height / 3.0)), int(round(2.0 * height / 3.0))]
    xs = [int(round(width / 3.0)), int(round(2.0 * width / 3.0))]
    half_window = max(6, min(height, width) // 12)

    samples: list[float] = []
    for y in ys:
        for x in xs:
            y0 = max(0, y - half_window)
            y1 = min(height, y + half_window)
            x0 = max(0, x - half_window)
            x1 = min(width, x + half_window)
            patch = grad[y0:y1, x0:x1]
            if patch.size:
                samples.append(float(np.mean(patch)))

    if not samples:
        return 0.0

    ratio = float(np.mean(samples)) / global_energy
    return _clamp(ratio / 2.0, 0.0, 1.0)


def analyze_image(path: Path) -> dict[str, float | int | str]:
    with Image.open(path) as img:
        gray = np.asarray(img.convert("L"), dtype=np.float32)

    height, width = gray.shape
    return {
        "file": path.name,
        "width": int(width),
        "height": int(height),
        "pixels": int(width * height),
        "brightness_mean": float(gray.mean()),
        "contrast_std": float(gray.std()),
        "sharpness_laplacian_var": laplacian_variance(gray),
        "dark_clip_pct": float((gray < 25).mean() * 100.0),
        "bright_clip_pct": float((gray > 245).mean() * 100.0),
        "composition_rule_of_thirds_score": composition_rule_of_thirds_score(gray),
    }


def score_quality(metrics: dict[str, float | int | str]) -> dict[str, float | int]:
    pixels = float(metrics["pixels"])
    sharpness = float(metrics["sharpness_laplacian_var"])
    contrast = float(metrics["contrast_std"])
    dark_clip = float(metrics["dark_clip_pct"])
    bright_clip = float(metrics["bright_clip_pct"])
    thirds_score = float(metrics["composition_rule_of_thirds_score"])

    resolution_score = _clamp(pixels / 2_000_000.0, 0.0, 1.0)
    sharpness_score = _clamp(math.log1p(sharpness) / 6.5, 0.0, 1.0)
    contrast_score = _clamp(contrast / 64.0, 0.0, 1.0)
    exposure_balance_score = 1.0 - _clamp((dark_clip + bright_clip) / 35.0, 0.0, 1.0)

    quality_score = (
        0.30 * sharpness_score
        + 0.25 * resolution_score
        + 0.20 * contrast_score
        + 0.15 * exposure_balance_score
        + 0.10 * thirds_score
    )
    quality_score = _clamp(quality_score, 0.0, 1.0)
    quality_bucket = int(round(quality_score * 99.0))

    return {
        "quality_resolution_score": resolution_score,
        "quality_sharpness_score": sharpness_score,
        "quality_contrast_score": contrast_score,
        "quality_exposure_score": exposure_balance_score,
        "quality_composition_score": thirds_score,
        "quality_score": quality_score,
        "quality_bucket": quality_bucket,
    }


def analyze_and_score(path: Path) -> dict[str, float | int | str]:
    payload = analyze_image(path)
    payload.update(score_quality(payload))
    return payload


def quality_sort_key(row: dict[str, float | int | str]) -> tuple[float, float, float, str]:
    sha = str(row.get("sha256", ""))
    return (
        -float(row["quality_score"]),
        -float(row["pixels"]),
        -float(row["sharpness_laplacian_var"]),
        sha,
    )

