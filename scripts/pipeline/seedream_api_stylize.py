#!/usr/bin/env python3
"""Stylize a real subject photo to anime using Seedream 5.0 Lite via BytePlus ModelArk API.

Seedream 5.0 Lite (model ID: seedream-5-0-260128) is an API-only model available
through BytePlus ModelArk. It is NOT downloadable from HuggingFace.

This script is called as a Scenario B pre-processing step to convert the real
subject image into an anime-stylized version before FLUX.2 composition.

API documentation: https://www.byteplus.com/en/blog/seedream5-0-lite

Usage:
  python scripts/pipeline/seedream_api_stylize.py \\
    --input /opt/aurora/data/character_refs/subject_photo.jpg \\
    --output /opt/aurora/ComfyUI/input/subject_anime.png \\
    --prompt "2D anime cel art style, flat shading, clean line art" \\
    --env-file configs/env/digitalocean_h100.env

Environment variables (can be set in env file):
  BYTEPLUS_API_KEY          - BytePlus ModelArk API key (required)
  BYTEPLUS_SEEDREAM_MODEL_ID - Model ID (default: seedream-5-0-260128)
  BYTEPLUS_API_ENDPOINT     - API base URL
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import time
from pathlib import Path

import requests


DEFAULT_PROMPT = (
    "2D anime cel animation style, flat cel shading, clean black outlines, "
    "Miyazaki-inspired color palette, watercolor background elements, "
    "preserve subject identity and facial structure"
)
DEFAULT_NEGATIVE = "photorealistic, live action, 3D render, CGI"


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _encode_image_b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _save_b64_image(b64_data: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(base64.b64decode(b64_data))


def _call_seedream_api(
    api_key: str,
    endpoint: str,
    model_id: str,
    prompt: str,
    negative_prompt: str,
    input_image_b64: str,
    width: int,
    height: int,
    guidance_scale: float,
    num_inference_steps: int,
    seed: int,
    timeout: int,
) -> dict:
    """Call the BytePlus ModelArk Seedream image-editing endpoint.

    The ModelArk API follows the OpenAI-compatible chat/images endpoint pattern.
    For image editing/stylization, the image is passed as a base64-encoded
    image_url in the content array alongside the text prompt.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # ModelArk image generation endpoint
    url = f"{endpoint.rstrip('/')}/images/generations"

    payload: dict = {
        "model": model_id,
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "size": f"{width}x{height}",
        "guidance_scale": guidance_scale,
        "num_inference_steps": num_inference_steps,
        "seed": seed,
        "response_format": "b64_json",
        # Pass input image for image-to-image stylization
        "image": f"data:image/jpeg;base64,{input_image_b64}",
        "image_strength": 0.65,  # How much to preserve input structure
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)

    if resp.status_code == 401:
        raise RuntimeError(
            "Seedream API: 401 Unauthorized. "
            "Check that BYTEPLUS_API_KEY is set correctly in your env file.\n"
            "Get your API key at: https://www.byteplus.com/en/product/modelark"
        )
    if resp.status_code == 404:
        raise RuntimeError(
            f"Seedream API: 404 Not Found at {url}. "
            "Check BYTEPLUS_API_ENDPOINT in your env file.\n"
            f"Response: {resp.text[:500]}"
        )
    if not resp.ok:
        raise RuntimeError(
            f"Seedream API error {resp.status_code}: {resp.text[:1000]}"
        )

    return resp.json()


def stylize(
    input_path: Path,
    output_path: Path,
    prompt: str,
    negative_prompt: str,
    api_key: str,
    endpoint: str,
    model_id: str,
    width: int = 1024,
    height: int = 1024,
    guidance_scale: float = 7.5,
    num_inference_steps: int = 30,
    seed: int = 42,
    timeout: int = 120,
    dry_run: bool = False,
) -> dict:
    if not input_path.exists():
        raise FileNotFoundError(f"Input image not found: {input_path}")

    input_b64 = _encode_image_b64(input_path)

    result: dict = {
        "input": str(input_path),
        "output": str(output_path),
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "model_id": model_id,
        "width": width,
        "height": height,
        "seed": seed,
        "dry_run": dry_run,
    }

    if dry_run:
        print(json.dumps({"dry_run": True, "would_call": endpoint, "model": model_id}, indent=2))
        return result

    t0 = time.monotonic()
    response = _call_seedream_api(
        api_key=api_key,
        endpoint=endpoint,
        model_id=model_id,
        prompt=prompt,
        negative_prompt=negative_prompt,
        input_image_b64=input_b64,
        width=width,
        height=height,
        guidance_scale=guidance_scale,
        num_inference_steps=num_inference_steps,
        seed=seed,
        timeout=timeout,
    )
    elapsed = time.monotonic() - t0

    # Extract image from response
    data = response.get("data", [])
    if not data:
        raise RuntimeError(f"Seedream API returned no image data. Response: {json.dumps(response)[:500]}")

    b64_image = data[0].get("b64_json", "")
    if not b64_image:
        raise RuntimeError("Seedream API response missing b64_json field.")

    _save_b64_image(b64_image, output_path)

    result["elapsed_seconds"] = round(elapsed, 2)
    result["api_response_keys"] = list(response.keys())
    print(f"Seedream stylization complete in {elapsed:.1f}s → {output_path}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stylize a real subject photo to anime using Seedream 5.0 Lite API"
    )
    parser.add_argument("--input", required=True, help="Input image path (real subject photo)")
    parser.add_argument("--output", required=True, help="Output image path (anime stylized)")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--negative-prompt", default=DEFAULT_NEGATIVE)
    parser.add_argument("--env-file", default="configs/env/digitalocean_h100.env")
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument("--height", type=int, default=1024)
    parser.add_argument("--guidance-scale", type=float, default=7.5)
    parser.add_argument("--steps", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--timeout", type=int, default=120, help="HTTP request timeout in seconds")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--manifest-out", default=None, help="Optional path to write result JSON")
    args = parser.parse_args()

    load_env_file(Path(args.env_file))

    api_key = os.getenv("BYTEPLUS_API_KEY", "")
    if not api_key or api_key.startswith("REPLACE_"):
        raise SystemExit(
            "BYTEPLUS_API_KEY is not set.\n"
            "Get your API key at https://www.byteplus.com/en/product/modelark "
            "and set BYTEPLUS_API_KEY in your env file."
        )

    model_id = os.getenv("BYTEPLUS_SEEDREAM_MODEL_ID", "seedream-5-0-260128")
    endpoint = os.getenv(
        "BYTEPLUS_API_ENDPOINT",
        "https://ark.ap-southeast.bytepluses.com/api/v3",
    )

    result = stylize(
        input_path=Path(args.input),
        output_path=Path(args.output),
        prompt=args.prompt,
        negative_prompt=args.negative_prompt,
        api_key=api_key,
        endpoint=endpoint,
        model_id=model_id,
        width=args.width,
        height=args.height,
        guidance_scale=args.guidance_scale,
        num_inference_steps=args.steps,
        seed=args.seed,
        timeout=args.timeout,
        dry_run=args.dry_run,
    )

    if args.manifest_out:
        Path(args.manifest_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.manifest_out).write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
