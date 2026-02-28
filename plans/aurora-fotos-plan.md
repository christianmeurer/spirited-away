# Advanced Methodologies for Multi-Domain Aesthetic Composition and Identity Preservation in Generative AI

## Full Implementation Plan

## Introduction to the 2026 Generative Landscape

The generative artificial intelligence landscape of 2026 has transitioned from stochastic, unpredictable image synthesis to highly deterministic, programmatic visual orchestration. To accomplish the complex routing problem of placing a real human subject alongside *Spirited Away*-inspired characters across three distinct aesthetic realities (photorealistic, 2D anime, and mixed-media), monolithic single-prompt interfaces are insufficient.

This document defines a SOTA implementation plan based on validated 2026 workflows, using a decoupled, node-based pipeline in ComfyUI. The system sequentially handles identity anchoring, aesthetic translation, and structural compositing through FLUX.2, Qwen-Edit 2511, and Seedream 5.0 Lite.

Operational constraints for this repository:
- Internal-only experimentation (`INTERNAL_RND`).
- No public sharing or distribution.
- Still-image outputs only.
- DigitalOcean-only infrastructure policy.

---

## Infrastructure Baseline (DigitalOcean-Only)

All compute paths in this plan execute on DigitalOcean resources.

- Primary compute: DigitalOcean GPU droplets (H100/H200 class where available).
- Optional orchestration: DigitalOcean Kubernetes GPU node pools.
- Internal artifact storage: DigitalOcean Spaces or internal filesystem paths.
- Provider fallback: additional DigitalOcean regions only (no cross-provider fallback).

---

## Phase I: Absolute Identity Anchoring via H200-Class Hardware

To prevent identity degradation when the subject is composited with stylized companions, train a dedicated identity adapter.

### Workflow: Training with AI Toolkit (RunComfy/Local)

#### Dataset Preparation
- Curate 15-30 high-quality images of the target subject across varied lighting and pose.
- Caption with a unique trigger token (example: `[subj_name_2026] person`).

#### Environment Setup
- Launch AI Toolkit configured for Tier-C class workloads (64GB+ VRAM).
- Load FLUX.2 [dev] checkpoint and matching tokenizer/text-encoder stack.

#### Hyperparameter Baseline for H200-Class Training
- Resolution bucket: native 1024x1024, optional scale to 1408x1408.
- Batch size: 2-4.
- Adapter rank: 32 or 64.
- Optimizer: AdamW8Bit.
- Learning rate: 0.0001.
- Weight decay: 0.0001.
- Timestep bias: balanced or slightly low-noise.
- Differential Output Preservation (DOP): enabled.

#### Identity Adapter Promotion Gate
- Pilot run must pass anatomy and identity thresholds for the target track.
- Adapter must be tagged `INTERNAL_RND` and bound to one track only.
- Cross-track adapter reuse is prohibited.
- Keep rollback pointer to previous passing adapter.

---

## Phase II: Aesthetic Translation of Assets

Before final composition, generate isolated assets for all three scenario requirements.

### Step 1: Generate Base Anime Assets
- Use FLUX.2 with a Miyazaki-style anime LoRA.
- Use structured prompts emphasizing flat cel shading and watercolor background logic.
- Output clean, isolated companion assets.

### Step 2: Photoreal Translation of Anime Companions (Scenario A)
- Use Qwen-Edit 2511 with Anything2Real 2601 A adapter.
- Load `pytorch_lora_weights.safetensors`.
- Prompt constraint: avoid style words such as `anime` or `illustration` during photoreal translation.
- Preferred instruction pattern: physical/photographic directives only.
- Tune LoRA strength in 0.8-1.0 range; reduce toward 0.5 if realism quality improves.

### Step 3: Anime Stylization of Real Subject (Scenario B)
- Use Seedream 5.0 Lite Edit.
- Input: real subject photo + anime style reference image.
- Prompt must preserve identity geometry and anatomy while enforcing 2D style intent.

---

## Phase III: Advanced Composition via ComfyUI

With prepared assets (identity adapter, photoreal companions, anime companions, anime-stylized subject), run final scenario composites.

### Scenario A: Real Person + Photoreal Inspired Companions (Track A)

#### Setup
- Load FLUX.2 [dev] checkpoint, text encoder, and VAE.
- Load custom subject identity adapter.

#### Routing
- Use multi-reference graph.
- Feed photoreal-translated companion assets into reference encoders.

#### Composition Prompting
- Invoke trigger token and physical interaction context.
- Enforce realistic light/shadow coherence and scene perspective.

### Scenario B: Anime-Stylized Person + 2D Anime Companions (Track B)

#### Setup
- Load FLUX.2 with anime-style LoRA.

#### Routing
- Feed anime subject asset + original 2D companions into multi-reference nodes.

#### Composition Prompting
- Target unified cel animation frame + watercolor environment.
- Reject photoreal bleed.

### Scenario C: Mixed-Media (Real Person + 2D Anime Companions)

This is the highest-complexity scenario and requires strict domain isolation.

#### Spatial Stitching & Geometry
- Use Kontext-style image edit graph.
- Stitch subject + companion assets into shared spatial frame.

#### Latent Encoding
- Route through VAE loader and encoder for latent editing.

#### Attention Pairing & Boundary Control
- Use TP-Blend-style attention pairing to confine photoreal embeddings to subject region and anime embeddings to companion regions.
- Add blend node for grounded environmental contact (Multiply/Soft Light with tuned `blend_factor`).

#### Multi-Pass Refinement
- Run low-denoise refinement pass to clean seam and contact transitions.
- Decode to final still output.

---

## Mandatory Quality Gates (Unchanged)

### Track A
- anatomy failure rate <= 0.015
- identity mean >= 0.86
- identity min >= 0.74
- style fidelity >= 0.80
- pairing score >= 0.84
- metadata completeness = 1.00

### Track B
- anatomy failure rate <= 0.020
- identity mean >= 0.82
- identity min >= 0.70
- style fidelity >= 0.88
- pairing score >= 0.90
- metadata completeness = 1.00

### Universal Internal Gates
- Human review approval required for each batch item.
- Manual final-candidate approval required at batch level.
- Pre-export guard must block non-internal destinations.
- Track isolation gate must pass.

### Deterministic Metric Definitions
- `anatomy_failure_rate = anatomy_fail_count / batch_size`
- `identity_mean = mean(identity_similarity_i)`
- `identity_min = min(identity_similarity_i)`
- `style_fidelity = mean(style_fidelity_i)`
- `pairing_score = mean(pairing_score_i)`
- `metadata_completeness = present_required_fields / total_required_fields`

---

## Reproducibility Contract

Each generated item must log:
- `prompt`
- `negative_prompt`
- `seed`
- `sampler`
- `scheduler`
- `base_model_hash`
- `adapter_versions`
- `postprocess_chain`
- `external_component_provenance`
- `external_component_licensing_notes`

Manifest-level required fields:
- `run_id`
- `track_id`
- `usage_scope`
- `internal_tag`
- `outputs`
- `sharing_allowed`
- `provider_policy_ack`
- `manual_final_candidate_approval`
- `batch`

---

## Provider Policy (DigitalOcean-Only)

Execution policy:
1. `digitalocean_primary_region`
2. `digitalocean_secondary_region`
3. `local_fallback_for_pre_post_only`

Operational controls:
- Pilot-before-scale remains mandatory.
- Checkpointed/resumable runs remain mandatory.
- No multi-cloud fallback.

---

## Scenario Acceptance Matrix

- Scenario A accepted only when Track A thresholds + universal gates pass.
- Scenario B accepted only when Track B thresholds + universal gates pass.
- Scenario C accepted only when declared target thresholds pass and human review confirms style-boundary integrity.

Scenario declaration and enforcement requirements:
- Scenario A manifests must set `scenario_id=scenario_a` and `track_id=track_a`.
- Scenario B manifests must set `scenario_id=scenario_b` and `track_id=track_b`.
- Scenario C manifests must set `scenario_id=scenario_c` and include `mixed_media_boundary_approval.approved=true`.
- Every batch item must include `scenario_checks` and pass all scenario-required checks from [`configs/scenarios/internal_rnd_scenarios.json`](configs/scenarios/internal_rnd_scenarios.json).
- Scenario policy gate is mandatory and enforced by [`validate_scenario_policy()`](scripts/internal_rnd_cli.py:130).

---

## Execution Checklist

- [ ] Prepare dataset and trigger token.
- [ ] Train/validate identity adapter pilot.
- [ ] Generate translation assets for Scenarios A and B.
- [ ] Prepare mixed-media masks/routing controls for Scenario C.
- [ ] Run scenario composition batches with deterministic seeds.
- [ ] Evaluate thresholds and metadata completeness.
- [ ] Complete human review and manual final approval.
- [ ] Run pre-export guard.
- [ ] Archive internal artifacts.

---

## Artifacts and Control Points

- [`configs/tracks/track_a.internal_rnd.json`](configs/tracks/track_a.internal_rnd.json)
- [`configs/tracks/track_b.internal_rnd.json`](configs/tracks/track_b.internal_rnd.json)
- [`scripts/internal_rnd_cli.py`](scripts/internal_rnd_cli.py)
- [`runbooks/internal-rnd-pipeline.md`](runbooks/internal-rnd-pipeline.md)
- [`manifests/examples/track_a_batch_example.internal_rnd.json`](manifests/examples/track_a_batch_example.internal_rnd.json)
- [`manifests/examples/track_b_batch_example.internal_rnd.json`](manifests/examples/track_b_batch_example.internal_rnd.json)

---

## Works Cited

- The Best Open-Source Image Generation Models in 2026 — Bento
- lrzjason/Anything2Real — Hugging Face
- Introducing ByteDance Seedream V5.0 Lite Sequential — WaveSpeed
- Training & Fine-Tuning on NVIDIA H200 — Uvation
- FLUX.2 [dev] LoRA Training Guide with Ostris AI Toolkit — RunComfy
- RunningHub Stable Diffusion & Flux LoRA
- [Flux2Klein 9B] Anything2Real lrzjason — Civitai
- flymy-ai/qwen-image-anime-irl-lora — Hugging Face
- AI Anime to Real Life Converter — nanabanana.pro
- FLUX BEST LORAs FOR MULTIPLE STYLES — YouTube
- Flux.2 Workflow with optional Multi-image reference — Reddit
- FLUX.2: Production Grade Image Generation With Multi-Reference — studio.aifilms.ai
- Flux 2 ComfyUI: 4 Workflows — ghibliart.ai
- Set Up Flux 2 dev in ComfyUI — sonusahani.com
- ComfyUI Flux.2 Dev Example — docs.comfy.org
- ComfyUI Flux Kontext Dev Grouped Workflow: Image Combo — comfyui.dev
- TP-Blend (paper)
- Image Blend — ComfyUI Wiki
- ComfyUI_PS_Blend_Node — comfyai.run
