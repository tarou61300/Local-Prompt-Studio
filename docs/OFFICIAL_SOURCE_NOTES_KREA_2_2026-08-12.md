# Official Source Notes — Krea 2

Verified: 2026-08-12

This note records why the bundled Krea 2 profile rules exist. Profile text is a Local Prompt Studio paraphrase, not a verbatim redistribution of Krea's prompt-expansion system prompt.

## Primary sources

- https://github.com/krea-ai/krea-2
- https://github.com/krea-ai/krea-2/blob/main/docs/prompting.md
- https://github.com/krea-ai/krea-2/blob/main/docs/expansion.txt
- https://github.com/krea-ai/krea-2/blob/main/encoder.py
- https://huggingface.co/krea/Krea-2-Raw
- https://huggingface.co/krea/Krea-2-Turbo

## Prompt behavior used by Local Prompt Studio

- Krea officially recommends natural-language prompts.
- The official prompting guide says long, detailed prompts generally work best, while the model can still produce high-quality images from minimal prompts.
- The official LLM expansion guidance prioritizes faithfulness: preserve subjects, actions, colors, spatial relationships, and explicit medium; avoid inventing new subjects/props and avoid over-specifying unsupported details.
- The official expansion guidance asks for one cohesive final paragraph rather than bullets, JSON, or Markdown.
- For visible text, the official prompting guidance recommends putting the exact requested words in quotation marks.
- Krea 2 open checkpoints are text-to-image. Local Prompt Studio therefore exposes only T2I in Phase 2A-2.
- The official open inference interface accepts a single prompt. No generic recommended negative prompt or universal quality-tag prefix is documented, so the Krea 2 profile intentionally leaves fixed positive/negative components empty.

## Raw and Turbo variants

Krea 2 Raw:
- Base/undistilled checkpoint.
- Official repository recommends Raw for fine-tuning/post-training and uses 52 inference steps with CFG 3.5 in its example.
- Official README says Raw was trained to generate up to 1K resolution.

Krea 2 Turbo:
- Distilled inference checkpoint.
- Official repository recommends Turbo for normal fast inference.
- Official example uses 8 steps, CFG 0.0, mu 1.15, and 2048x2048.
- Official README describes Turbo as supporting roughly 1K to 2K resolution.

These inference settings are reference metadata only. Local Prompt Studio does not change ComfyUI sampler settings.

## Length handling

The official Krea 2 text encoder config uses max_length=512 and truncation. This is a technical encoder capacity, not a recommendation to force prompts to exactly 512 tokens.

Local Prompt Studio's current generic length validator does not use Krea's exact tokenizer. Therefore the Krea 2 variants intentionally leave `length_guidance` empty rather than presenting an inaccurate token counter or truncating user content.

The technical 512-token encoder information is stored only in `inference_recommendations` as reference metadata.

This follows the Local Prompt Studio Core Transformation Policy:
- preserve user content first;
- do not delete or pad content to satisfy a target length;
- do not run an automatic compression pass solely for length.

## Local Prompt Studio implementation choice

Krea 2 uses the new `natural_language` renderer.

This renderer deliberately keeps the same deterministic fixed-component and exact-preservation contract as `video_narrative`, but the Krea profile's instructions produce a single natural-language image paragraph.

No Krea-specific model-name branch is required in `PromptEngine` or `MainWindow`.
The existing catalog-driven category/model/variant/task UI is sufficient for:
- Category: Image
- Model: Krea 2
- Variant: Raw / Turbo
- Task: T2I

## Scope exclusions

Phase 2A-2 does not add:
- image-to-image or image editing for Krea 2;
- automatic image inspection;
- automatic ComfyUI sampler changes;
- a negative prompt for Krea 2;
- generic quality tags;
- strict token-length enforcement;
- Anima or Danbooru-tag rendering.
