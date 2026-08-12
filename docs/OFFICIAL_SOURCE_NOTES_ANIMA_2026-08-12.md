# Anima Official Source Notes — 2026-08-12

These notes are the research basis for the bundled Local Prompt Studio `anima`
profile. They are documentation/provenance only. The application does not fetch
these URLs at startup or during normal prompt generation.

## Primary official source

- CircleStone Labs Anima model card:
  https://huggingface.co/circlestone-labs/Anima
- Official diffusion model files:
  https://huggingface.co/circlestone-labs/Anima/tree/main/split_files/diffusion_models

Verified: 2026-08-12.

## Official model variants used by this profile

- Anima-Base v1.0
  - checkpoint: `anima-base-v1.0.safetensors`
  - official description: pretrained/unrefined base, maximum flexibility,
    diversity, and style adherence; intended base for LoRA training.
- Anima-Aesthetic v1.1
  - checkpoint: `anima-aesthetic-v1.1.safetensors`
  - latest Aesthetic checkpoint present in the official file tree on the
    verification date.
  - Aesthetic prompting guidance says quality tags are not required and advises
    against `score_*` tags in both positive and negative prompts.
- Anima-Turbo v1.0
  - checkpoint: `anima-turbo-v1.0.safetensors`
  - distilled version for fast generation.
  - official guidance: CFG 1 and 8-12 steps.
  - official card recommends starting with Turbo for convenient prompt
    iteration.

The model card gives general non-Turbo generation guidance of 30-50 steps,
CFG 4-5, and working resolutions from 512^2 to 1536^2 pixels.

## Official prompting guidance represented in the profile

The model is trained on Danbooru-style tags, natural-language captions, and
mixtures of both. The official tag rules state:

- ordinary tags are lowercase;
- spaces are used instead of underscores;
- score tags retain underscores;
- when Danbooru and Gelbooru differ, prefer the Gelbooru form;
- artist tags require an `@` prefix;
- recommended tag-section order is:
  quality/meta/year/safety -> subject count -> character -> series -> artist ->
  general tags;
- tag order within each section may vary;
- tag dropout was used during training, so every possible tag is not required;
- extremely short/underdetailed prompts can lead to undesired output.

The current official recommended positive prefix is:

`masterpiece, best quality, score_7, safe`

The current official recommended negative list is:

`worst quality, low quality, score_1, score_2, score_3, artist name, blurry, jpeg artifacts, chromatic aberration`

For Anima-Aesthetic, the official card recommends not using `score_*` tags in
either positive or negative. `masterpiece, best quality` is described as safe to
leave in.

The official card also notes that text rendering is limited: single words and
some short phrases may work, while lengthy text is unreliable.

## Local Prompt Studio policy decisions

These are Studio implementation choices, not claims that the model author
requires them:

1. The LLM returns a small JSON object containing ordered tag categories rather
   than the final comma-separated prompt. This lets the renderer enforce
   deterministic section ordering and fixed profile components without trusting
   the LLM to remember them.
2. Base/Turbo use the current official recommended positive/negative components.
3. Aesthetic removes all `score_*` fixed components in accordance with the
   Aesthetic prompting guidance.
4. `safe` is normally part of the official recommended prefix. If the user
   explicitly requests `sensitive`, `nsfw`, or `explicit`, the renderer removes
   conflicting default `safe` so Core Transformation Policy does not contradict
   explicit user intent.
5. No numeric target tag count is invented. Prompt length/detail remains
   qualitative guidance only.
6. Positive and Negative prompts are shown separately. The legacy MMH3 Prompt
   Bridge has one selected target, so Send to ComfyUI continues to send only the
   editable Positive Prompt. Negative is copied manually.
7. Quantized exports are not separate profile variants unless their prompting
   rules differ.
