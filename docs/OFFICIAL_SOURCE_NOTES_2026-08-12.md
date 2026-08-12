# Official Source Notes — Wan 2.2 and LTX-2.3

Verified: 2026-08-12

This note records why the bundled profile rules exist. Profile text is a Local Prompt Studio paraphrase, not a verbatim redistribution of upstream system prompts.

## Wan 2.2

Primary sources:

- https://github.com/Wan-Video/Wan2.2
- https://github.com/Wan-Video/Wan2.2/blob/main/wan/utils/prompt_extend.py
- https://github.com/Wan-Video/Wan2.2/blob/main/wan/utils/system_prompt.py

Supported initial Local Prompt Studio tasks:

- T2V: Wan2.2-T2V-A14B
- I2V: Wan2.2-I2V-A14B

Relevant upstream behavior:

- The official repository recommends prompt extension because richer prompts can improve video detail/quality.
- The official T2V English prompt-extension template targets roughly 60-200 words and adds cinematic/visual detail while preserving the core subject/action.
- The official I2V English prompt-extension template is strongly action-focused, avoids repeating static image content, preserves camera movement, and targets 100 words or fewer.
- The upstream I2V expander can inspect the input image through a vision-language model. Local Prompt Studio currently cannot, so its profile must not pretend to know source-image content.
- Upstream prompt-extension safety replacement rules are not copied into the Local Prompt Studio profile. They are service/model-side policies rather than prompt-format requirements and can conflict with Local Prompt Studio's semantic-preservation contract.
- T2V-A14B/I2V-A14B are treated as video-only targets here; the profile must not invent an audio soundscape.

Schema note:

Profile Schema v1 has one variant-wide `length_guidance`. Wan's official guidance differs by task (T2V versus I2V), so no numeric variant-wide limit is stored. The task-specific values remain advisory in `instructions.md` until the schema supports task-specific guidance.

## LTX-2.3

Primary sources:

- https://github.com/Lightricks/LTX-2
- https://huggingface.co/Lightricks/LTX-2.3
- https://github.com/Lightricks/LTX-2/blob/main/packages/ltx-core/src/ltx_core/text_encoders/gemma/encoders/prompts/gemma_t2v_system_prompt.txt
- https://github.com/Lightricks/LTX-2/blob/main/packages/ltx-core/src/ltx_core/text_encoders/gemma/encoders/prompts/gemma_i2v_system_prompt.txt
- https://github.com/Lightricks/LTX-2/blob/main/packages/ltx-pipelines/docs/pipelines.md

Relevant upstream behavior:

- Official prompting guidance recommends a single flowing paragraph, chronological scene/action description, literal and precise wording, and no more than 200 words.
- Official T2V prompt enhancement integrates visual and audio descriptions chronologically, preserves requested dialogue, does not invent camera motion, and avoids timestamps/cuts unless requested.
- Official I2V enhancement focuses on changes from the first frame and avoids repeating established static image details.
- Local Prompt Studio currently does not inspect the source image, so I2V rules must focus on user-described motion/changes without inventing first-frame facts.
- LTX-2.3 supports synchronized video/audio. Literal speech such as `[speech:ja] ...` should be preserved exactly and represented as non-English speech without translating the quoted text.
- The official repository exposes both 22B Dev and 22B Distilled 1.1 checkpoints.
- The official DistilledPipeline uses 8 predefined sigmas for stage 1 and 4 refinement steps for stage 2 and does not require guidance.
- The production-quality non-distilled path is the two-stage TI2Vid pipeline.

## Local Prompt Studio policy overrides

For both profiles, upstream recommendations are subordinate to the Local Prompt Studio Core Transformation Policy:

- User intent wins over stylistic expansion.
- Literal Content and Protected Terms are exact-preservation data.
- Length is guidance, not a reason to delete, add, compress, or rewrite semantic content.
- Faithful mode does not invent unspecified semantic details even where an upstream auto-enhancer may normally do so.
