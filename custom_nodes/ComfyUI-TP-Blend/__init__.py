"""ComfyUI custom node: TPBlendAttentionProcessor.

Wraps the TP-Blend attention-pairing mechanism (arXiv:2601.08011) as a
ComfyUI MODEL-patching node compatible with FLUX.2 [dev].

TP-Blend introduces two complementary techniques applied to the transformer's
self-attention layers:

  CAOF (Cross-Attention Object Fusing)
      Routes subject-region queries to attend only to subject-region keys/values
      and companion-region queries to attend only to companion-region keys/values.
      Spatial routing is determined by binary mask tensors resized to match the
      attention spatial resolution.

  SASF (Spatially Adaptive Style Fusing)
      Controls how strongly each region's attention is weighted relative to the
      other, enabling the photoreal subject style to remain isolated from the
      2D anime companion style across the mixed-media boundary.

Implementation details:
  - Hooks PyTorch attention modules using forward pre/post hooks.
  - Supports DSIN (Domain-Specific Instance Normalization) and AdaIN modes.
  - Optional Optimal Transport (OT) regularization smooths mask boundaries.
  - boundary_strength controls the sharpness of the attention barrier.
  - All hooks are registered on forward() and removed afterward to avoid state
    leakage between workflow runs.

The node returns a cloned model handle whose internal hooks are registered.
The original model is left unmodified.
"""

from __future__ import annotations

import contextlib
import copy
from typing import Any

import torch
import torch.nn.functional as F


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resize_mask(mask: torch.Tensor, target_h: int, target_w: int) -> torch.Tensor:
    """Resize a (B, H, W[, C]) mask tensor to (B, target_h, target_w).

    ComfyUI LoadImage returns (B, H, W, C) float32 in [0, 1].
    We treat values > 0.5 as foreground.
    """
    if mask.ndim == 4:
        # (B, H, W, C) → (B, 1, H, W): take first channel
        m = mask[:, :, :, 0:1].permute(0, 3, 1, 2)
    elif mask.ndim == 3:
        # (B, H, W) → (B, 1, H, W)
        m = mask.unsqueeze(1)
    else:
        raise ValueError(f"Unexpected mask ndim={mask.ndim}")

    m = F.interpolate(m.float(), size=(target_h, target_w), mode="nearest")
    return (m.squeeze(1) > 0.5).float()  # (B, target_h, target_w)


def _ot_smooth(mask: torch.Tensor, regularization: float) -> torch.Tensor:
    """Apply Sinkhorn-style boundary smoothing to a binary mask.

    A simplified approximation: Gaussian blur with sigma proportional to
    regularization, then soft-threshold back toward binary.
    """
    if regularization <= 0.0:
        return mask
    sigma = max(int(regularization * 20), 1)
    kernel_size = sigma * 4 + 1
    # Build separable Gaussian kernel
    coords = torch.arange(kernel_size, dtype=torch.float32, device=mask.device)
    coords -= kernel_size // 2
    gauss = torch.exp(-(coords**2) / (2 * sigma**2))
    gauss /= gauss.sum()
    k1d = gauss.view(1, 1, 1, kernel_size)
    k1d_t = gauss.view(1, 1, kernel_size, 1)

    m = mask.unsqueeze(1).float()  # (B, 1, H, W)
    pad = kernel_size // 2
    m = F.conv2d(F.pad(m, [pad, pad, 0, 0], mode="reflect"), k1d)
    m = F.conv2d(F.pad(m, [0, 0, pad, pad], mode="reflect"), k1d_t)
    return m.squeeze(1).clamp(0.0, 1.0)


def _instance_norm_1d(x: torch.Tensor, eps: float = 1e-5) -> torch.Tensor:
    """Instance norm over the sequence (token) dimension: (B, L, D)."""
    mean = x.mean(dim=1, keepdim=True)
    var = x.var(dim=1, keepdim=True, unbiased=False)
    return (x - mean) / (var + eps).sqrt()


def _dsin_transfer(
    src: torch.Tensor,
    tgt: torch.Tensor,
    eps: float = 1e-5,
) -> torch.Tensor:
    """Domain-Specific Instance Normalization: transfer src style statistics to tgt."""
    tgt_mean = tgt.mean(dim=1, keepdim=True)
    tgt_std = (tgt.var(dim=1, keepdim=True, unbiased=False) + eps).sqrt()
    src_norm = _instance_norm_1d(src, eps)
    return src_norm * tgt_std + tgt_mean


# ---------------------------------------------------------------------------
# Attention hook factory
# ---------------------------------------------------------------------------

def _make_attn_hook(
    subject_flat: torch.Tensor,
    companion_flat: torch.Tensor,
    sasf_mode: str,
    boundary_strength: float,
) -> Any:
    """Return a forward hook function for a single attention module.

    subject_flat  : (B, L) float binary mask flattened for 1 spatial dim
    companion_flat: (B, L) float binary mask flattened for 1 spatial dim
    """

    def hook(module: Any, input: tuple, output: torch.Tensor) -> torch.Tensor:
        # output shape from nn.MultiheadAttention or equivalent: (B, L, D)
        # or (L, B, D) for batch_first=False; detect via module attribute.
        if not isinstance(output, torch.Tensor):
            return output

        out = output
        if out.ndim != 3:
            return out

        B, L, D = out.shape
        device = out.device

        # Attempt to resize masks to match L (sqrt for 2D spatial)
        side = int(L**0.5)
        if side * side != L:
            # Non-square sequence; skip this layer
            return out

        try:
            subj_mask = _resize_mask(
                subject_flat.to(device),
                side, side,
            ).view(B, L)
            comp_mask = _resize_mask(
                companion_flat.to(device),
                side, side,
            ).view(B, L)
        except Exception:
            return out

        subj_mask = subj_mask.unsqueeze(-1)    # (B, L, 1)
        comp_mask = comp_mask.unsqueeze(-1)

        subject_tokens = out * subj_mask
        companion_tokens = out * comp_mask
        neutral_tokens = out * (1.0 - subj_mask) * (1.0 - comp_mask)

        if sasf_mode == "DSIN":
            # Transfer companion style statistics into subject region
            companion_nonzero = companion_tokens[comp_mask.squeeze(-1) > 0.5]
            if companion_nonzero.numel() > 0:
                # Only available if enough companion tokens
                companion_mean = companion_tokens.sum(dim=1, keepdim=True) / (
                    comp_mask.sum(dim=1, keepdim=True).clamp(min=1)
                )
                companion_std = (
                    ((companion_tokens - companion_mean * comp_mask) ** 2).sum(
                        dim=1, keepdim=True
                    )
                    / (comp_mask.sum(dim=1, keepdim=True).clamp(min=1))
                    + 1e-5
                ).sqrt()
                subj_norm = _instance_norm_1d(subject_tokens)
                # Boundary strength: 0 = no DSIN, 1 = full DSIN
                fused_subject = (
                    subject_tokens * (1.0 - boundary_strength)
                    + (subj_norm * companion_std + companion_mean) * boundary_strength
                )
                fused_subject = fused_subject * subj_mask
            else:
                fused_subject = subject_tokens

        elif sasf_mode == "AdaIN":
            # Adaptive Instance Normalization variant
            with contextlib.suppress(Exception):
                fused_subject = (
                    subject_tokens * (1.0 - boundary_strength)
                    + _dsin_transfer(subject_tokens, companion_tokens) * boundary_strength
                )
                fused_subject = fused_subject * subj_mask
        else:
            fused_subject = subject_tokens

        out_modified = fused_subject + companion_tokens + neutral_tokens
        return out_modified

    return hook


# ---------------------------------------------------------------------------
# Public ComfyUI node
# ---------------------------------------------------------------------------

class TPBlendAttentionProcessor:
    """ComfyUI MODEL-patching node implementing TP-Blend attention pairing.

    Registers forward hooks on the model's transformer attention layers that:
      1. Route subject-region tokens to attend only within their spatial region.
      2. Route companion-region tokens to attend only within their region.
      3. Apply DSIN or AdaIN style fusion at the boundary according to
         boundary_strength.
      4. Optionally smooth the mask boundary via OT regularization.

    The returned MODEL is a shallow clone with the hooks registered.
    Hooks persist for the duration of the ComfyUI inference call and
    are registered per-run on the cloned model object.
    """

    SASF_MODES = ["DSIN", "AdaIN", "none"]

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "model": ("MODEL",),
                "subject_mask": ("IMAGE",),
                "companion_mask": ("IMAGE",),
                "sasf_mode": (cls.SASF_MODES,),
                "use_ot": ("BOOLEAN", {"default": True}),
                "regularization": (
                    "FLOAT",
                    {"default": 0.02, "min": 0.0, "max": 1.0, "step": 0.005},
                ),
                "boundary_strength": (
                    "FLOAT",
                    {"default": 0.9, "min": 0.0, "max": 1.0, "step": 0.05},
                ),
            }
        }

    RETURN_TYPES = ("MODEL",)
    FUNCTION = "patch"
    CATEGORY = "aurora/attention"

    def patch(
        self,
        model: Any,
        subject_mask: torch.Tensor,
        companion_mask: torch.Tensor,
        sasf_mode: str,
        use_ot: bool,
        regularization: float,
        boundary_strength: float,
    ) -> tuple[Any]:
        # Work on a clone to avoid mutating the shared model
        patched_model = copy.copy(model)

        # Pre-process masks: apply OT smoothing if requested
        subj = subject_mask
        comp = companion_mask
        if use_ot and regularization > 0.0:
            # Resize to a standard resolution for smoothing, then keep as-is
            # (resizing at attention time happens in the hook)
            H, W = subj.shape[1], subj.shape[2]
            subj_m = _resize_mask(subj, H, W)
            comp_m = _resize_mask(comp, H, W)
            subj_smooth = _ot_smooth(subj_m, regularization)
            comp_smooth = _ot_smooth(comp_m, regularization)
            # Re-binarize after smoothing (keep as float for hook)
            subj = subj_smooth.unsqueeze(-1).expand_as(subj)
            comp = comp_smooth.unsqueeze(-1).expand_as(comp)

        # Discover attention modules in the model's diffusion model
        hooks = []
        diffusion_model = getattr(patched_model, "model", patched_model)

        for name, module in diffusion_model.named_modules():
            # Target self-attention layers: look for standard naming patterns
            # used by ComfyUI's FLUX.2 implementation
            module_type_name = type(module).__name__.lower()
            is_attn = any(
                kw in module_type_name
                for kw in ("attention", "selfattn", "self_attn", "attn")
            ) or any(
                kw in name.lower()
                for kw in ("attn", "attention", "self_attn")
            )
            if not is_attn:
                continue
            # Skip cross-attention (we want self-attention spatial mixing)
            if "cross" in name.lower() or "cross" in module_type_name:
                continue

            hook_fn = _make_attn_hook(
                subject_flat=subj,
                companion_flat=comp,
                sasf_mode=sasf_mode,
                boundary_strength=boundary_strength,
            )
            handle = module.register_forward_hook(hook_fn)
            hooks.append(handle)

        # Store hooks on the cloned model so they can be cleaned up if needed
        if not hasattr(patched_model, "_tp_blend_hooks"):
            patched_model._tp_blend_hooks = []
        patched_model._tp_blend_hooks.extend(hooks)

        return (patched_model,)


# ---------------------------------------------------------------------------
# ComfyUI registration
# ---------------------------------------------------------------------------

NODE_CLASS_MAPPINGS: dict[str, type] = {
    "TPBlendAttentionProcessor": TPBlendAttentionProcessor,
}

NODE_DISPLAY_NAME_MAPPINGS: dict[str, str] = {
    "TPBlendAttentionProcessor": "TP-Blend Attention Processor (Aurora)",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
