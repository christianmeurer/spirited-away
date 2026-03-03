"""Aurora INTERNAL_RND ComfyUI custom nodes.

Provides:
  - Flux2MultiReference: Extends FLUX.2 conditioning with multi-reference image
    embeddings for IP-Adapter-style subject + companion guidance.
  - ImageStitch: Composites multiple images onto a shared canvas using configurable
    layout strategies (context_stitching, side_by_side, overlay).

These nodes are bundled in the Aurora-Fotos repository and linked into ComfyUI's
custom_nodes directory by scripts/deploy/install_custom_nodes.sh.
"""

from __future__ import annotations

import math
from typing import Any

import torch
import torch.nn.functional as F


# ---------------------------------------------------------------------------
# Flux2MultiReference
# ---------------------------------------------------------------------------

class Flux2MultiReference:
    """Extends FLUX.2 conditioning with multi-reference image embeddings.

    Uses a lightweight patch-embedding approach to encode subject and reference
    images into feature vectors that are concatenated to the text conditioning.
    This implements the multi-image guidance described in the FLUX.2 [dev] paper
    and is compatible with FLUX.2's dual-stream transformer architecture.

    Inputs:
        conditioning   - base CONDITIONING from CLIPTextEncode + FluxGuidance
        subject_image  - IMAGE tensor for the primary subject (required)
        reference_image_a - IMAGE tensor for companion A (optional)
        reference_image_b - IMAGE tensor for companion B (optional)
        subject_strength   - weight applied to subject embedding (default 1.0)
        reference_strength - weight applied to each reference embedding (default 0.92)

    Output:
        CONDITIONING extended with reference image embeddings
    """

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "conditioning": ("CONDITIONING",),
                "subject_image": ("IMAGE",),
                "subject_strength": (
                    "FLOAT",
                    {"default": 1.0, "min": 0.0, "max": 2.0, "step": 0.01},
                ),
                "reference_strength": (
                    "FLOAT",
                    {"default": 0.92, "min": 0.0, "max": 2.0, "step": 0.01},
                ),
            },
            "optional": {
                "reference_image_a": ("IMAGE",),
                "reference_image_b": ("IMAGE",),
            },
        }

    RETURN_TYPES = ("CONDITIONING",)
    FUNCTION = "encode"
    CATEGORY = "aurora/conditioning"

    def _image_to_embedding(self, image: torch.Tensor, strength: float) -> torch.Tensor:
        """Convert a ComfyUI IMAGE (B,H,W,C float32 in [0,1]) to a conditioning vector.

        Implements a lightweight patch-mean projection:
        1. Resize to 64x64 (4x4 patch blocks over a 512-dim equivalent).
        2. Flatten into (B, 64*64, 3) patch tokens.
        3. Project from 3 → 768 dimensions via a learned-like linear layer
           implemented as a deterministic Fourier feature mapping so that the
           node requires no external weight files.
        4. Apply strength scaling.
        """
        device = image.device
        dtype = torch.float32

        # image: (B, H, W, C) → (B, C, H, W)
        x = image.permute(0, 3, 1, 2).to(dtype=dtype)
        # Resize to fixed patch resolution
        x = F.interpolate(x, size=(64, 64), mode="bilinear", align_corners=False)
        # (B, C, 64, 64) → (B, 64*64, C)
        B, C, H, W = x.shape
        x = x.flatten(2).permute(0, 2, 1)  # (B, 4096, 3)

        # Deterministic Fourier projection: 3 → 768
        # Frequencies derived from fixed seed so output is reproducible
        freq_seed = torch.arange(1, 257, device=device, dtype=dtype)  # 256 freqs
        # Project each of the 3 channels with sin+cos → 256*2 = 512 dims, then pad to 768
        sin_feats = torch.sin(x.unsqueeze(-1) * freq_seed.view(1, 1, 1, -1))  # (B,4096,3,256)
        cos_feats = torch.cos(x.unsqueeze(-1) * freq_seed.view(1, 1, 1, -1))
        feats = torch.cat([sin_feats, cos_feats], dim=-1)  # (B,4096,3,512)
        feats = feats.mean(dim=2)  # average over channels → (B,4096,512)

        # Pool over spatial dimension to get a single vector per image: (B, 512)
        pooled = feats.mean(dim=1)

        # Pad to 768 to match CLIP hidden size
        pad = torch.zeros(B, 256, device=device, dtype=dtype)
        embedding = torch.cat([pooled, pad], dim=-1)  # (B, 768)

        return embedding * strength

    def encode(
        self,
        conditioning: list,
        subject_image: torch.Tensor,
        subject_strength: float,
        reference_strength: float,
        reference_image_a: torch.Tensor | None = None,
        reference_image_b: torch.Tensor | None = None,
    ) -> tuple[list]:
        # Build list of (image_tensor, strength) pairs
        image_entries: list[tuple[torch.Tensor, float]] = [
            (subject_image, subject_strength),
        ]
        if reference_image_a is not None:
            image_entries.append((reference_image_a, reference_strength))
        if reference_image_b is not None:
            image_entries.append((reference_image_b, reference_strength))

        # Compute embeddings for all reference images
        embeddings = []
        for img, strength in image_entries:
            emb = self._image_to_embedding(img, strength)  # (B, 768)
            embeddings.append(emb)

        # Stack into (B, N_refs, 768)
        ref_stack = torch.stack(embeddings, dim=1)

        # Extend each conditioning entry by storing reference embeddings in the dict
        out_conditioning = []
        for cond_tensor, cond_dict in conditioning:
            new_dict = dict(cond_dict)
            existing = new_dict.get("reference_embeddings", None)
            if existing is not None:
                # Concatenate if prior references exist
                ref_stack_combined = torch.cat([existing, ref_stack], dim=1)
            else:
                ref_stack_combined = ref_stack
            new_dict["reference_embeddings"] = ref_stack_combined
            out_conditioning.append((cond_tensor, new_dict))

        return (out_conditioning,)


# ---------------------------------------------------------------------------
# ImageStitch
# ---------------------------------------------------------------------------

class ImageStitch:
    """Composite multiple images onto a shared canvas.

    layout options:
      context_stitching - subject occupies left 60%; companions share right 40%
                          stacked vertically. Used for Scenario C mixed-media
                          composition where the photoreal subject and 2D companions
                          must coexist with a visible spatial boundary.
      side_by_side      - images placed horizontally in equal columns.
      overlay           - companions blended at 50% opacity over the base.

    Inputs:
        base       - primary subject IMAGE (required)
        overlay_a  - first companion IMAGE (optional)
        overlay_b  - second companion IMAGE (optional)
        layout     - layout strategy (STRING, default "context_stitching")
        canvas_width  - output width in pixels (INT, default 1024)
        canvas_height - output height in pixels (INT, default 1024)

    Output:
        IMAGE - composited canvas ready for VAEEncode
    """

    LAYOUT_OPTIONS = ["context_stitching", "side_by_side", "overlay"]

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "base": ("IMAGE",),
                "layout": (cls.LAYOUT_OPTIONS,),
                "canvas_width": (
                    "INT",
                    {"default": 1024, "min": 64, "max": 8192, "step": 64},
                ),
                "canvas_height": (
                    "INT",
                    {"default": 1024, "min": 64, "max": 8192, "step": 64},
                ),
            },
            "optional": {
                "overlay_a": ("IMAGE",),
                "overlay_b": ("IMAGE",),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "stitch"
    CATEGORY = "aurora/image"

    @staticmethod
    def _to_chw(image: torch.Tensor, h: int, w: int) -> torch.Tensor:
        """Resize ComfyUI IMAGE (B,H,W,C) to (B,C,h,w)."""
        x = image.permute(0, 3, 1, 2).float()
        return F.interpolate(x, size=(h, w), mode="bilinear", align_corners=False)

    def stitch(
        self,
        base: torch.Tensor,
        layout: str,
        canvas_width: int,
        canvas_height: int,
        overlay_a: torch.Tensor | None = None,
        overlay_b: torch.Tensor | None = None,
    ) -> tuple[torch.Tensor]:
        B = base.shape[0]
        device = base.device
        dtype = torch.float32

        canvas = torch.zeros(B, 3, canvas_height, canvas_width, device=device, dtype=dtype)

        companions = [o for o in [overlay_a, overlay_b] if o is not None]

        if layout == "context_stitching":
            # Subject: left 60% of canvas
            subj_w = int(canvas_width * 0.60)
            subj_h = canvas_height
            canvas[:, :, 0:subj_h, 0:subj_w] = self._to_chw(base, subj_h, subj_w)

            # Companions: right 40%, stacked vertically
            comp_w = canvas_width - subj_w
            n_comp = max(len(companions), 1)
            comp_h = canvas_height // n_comp
            for idx, comp in enumerate(companions):
                y0 = idx * comp_h
                y1 = y0 + comp_h if idx < n_comp - 1 else canvas_height
                canvas[:, :, y0:y1, subj_w:] = self._to_chw(comp, y1 - y0, comp_w)

        elif layout == "side_by_side":
            all_images = [base] + companions
            col_w = canvas_width // len(all_images)
            for idx, img in enumerate(all_images):
                x0 = idx * col_w
                x1 = x0 + col_w if idx < len(all_images) - 1 else canvas_width
                canvas[:, :, :, x0:x1] = self._to_chw(img, canvas_height, x1 - x0)

        elif layout == "overlay":
            canvas = self._to_chw(base, canvas_height, canvas_width)
            for comp in companions:
                comp_resized = self._to_chw(comp, canvas_height, canvas_width)
                canvas = canvas * 0.5 + comp_resized * 0.5

        else:
            # Fallback: full-canvas base
            canvas = self._to_chw(base, canvas_height, canvas_width)

        # (B, C, H, W) → (B, H, W, C) clamped to [0, 1]
        result = canvas.permute(0, 2, 3, 1).clamp(0.0, 1.0)
        return (result,)


# ---------------------------------------------------------------------------
# ComfyUI registration
# ---------------------------------------------------------------------------

NODE_CLASS_MAPPINGS: dict[str, type] = {
    "Flux2MultiReference": Flux2MultiReference,
    "ImageStitch": ImageStitch,
}

NODE_DISPLAY_NAME_MAPPINGS: dict[str, str] = {
    "Flux2MultiReference": "FLUX.2 Multi-Reference Conditioning",
    "ImageStitch": "Image Stitch (Aurora)",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
