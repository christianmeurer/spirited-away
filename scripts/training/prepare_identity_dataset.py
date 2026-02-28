#!/usr/bin/env python3
"""Prepare identity training dataset with trigger-token captions."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


SUPPORTED_EXT = {".jpg", ".jpeg", ".png", ".webp"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare identity dataset with captions")
    parser.add_argument("--input-dir", required=True, help="Source directory with subject images")
    parser.add_argument("--output-dir", required=True, help="Output dataset directory")
    parser.add_argument("--trigger-token", required=True, help="Unique trigger token (e.g. [subj_name_2026])")
    parser.add_argument("--caption-suffix", default="person", help="Caption suffix")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    image_dir = output_dir / "images"
    caption_dir = output_dir / "captions"

    image_dir.mkdir(parents=True, exist_ok=True)
    caption_dir.mkdir(parents=True, exist_ok=True)

    candidates = [
        p for p in sorted(input_dir.rglob("*")) if p.is_file() and p.suffix.lower() in SUPPORTED_EXT
    ]
    if not candidates:
        raise SystemExit(f"No supported images found in: {input_dir}")

    manifest_items = []
    for idx, src in enumerate(candidates, start=1):
        dst_name = f"subject_{idx:04d}{src.suffix.lower()}"
        dst_img = image_dir / dst_name
        shutil.copy2(src, dst_img)

        caption_text = f"{args.trigger_token} {args.caption_suffix}".strip()
        dst_caption = caption_dir / f"subject_{idx:04d}.txt"
        dst_caption.write_text(caption_text + "\n", encoding="utf-8")

        manifest_items.append(
            {
                "source_path": str(src),
                "image_path": str(dst_img),
                "caption_path": str(dst_caption),
                "sha256": sha256_file(dst_img),
                "caption": caption_text,
            }
        )

    manifest = {
        "trigger_token": args.trigger_token,
        "caption_suffix": args.caption_suffix,
        "image_count": len(manifest_items),
        "items": manifest_items,
    }
    (output_dir / "dataset_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(json.dumps({"prepared": len(manifest_items), "output_dir": str(output_dir)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

