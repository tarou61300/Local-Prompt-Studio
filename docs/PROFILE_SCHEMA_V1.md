# Local Prompt Studio Profile Schema v1

Phase 2A-5 supports the MiniMax H3, Wan 2.2, and LTX-2.3 video profiles plus the Krea 2 and Anima image profiles.
Profiles are UTF-8 data, never executable plugins. A profile may contain JSON,
Markdown, and text files. Python,
JavaScript, PowerShell, batch files, executables, DLLs, absolute references,
and path traversal are rejected.

## Layout

```text
profiles/<category>/<profile_id>/
    manifest.json
    instructions.md
    variants/<variant_id>.json
    locales/<locale_id>.json       # reserved/optional
```

Builtin profiles ship under `profiles/`. Future official updates use
`data/profiles/official/`; user-managed profiles use `data/profiles/custom/`.
For an identical official profile ID, official update data takes precedence
over builtin data. Custom IDs are separate and cannot silently replace an
official/builtin ID. A broken custom profile is rejected without preventing
other profiles from loading.

## Manifest

`schema_version` is the file specification version. `profile_version` versions
prompt rules. Optional `target_model_version` belongs to a variant and describes
the intended target model; it is not the profile version.

Required manifest concepts are stable `id`, display `name`, `profile_version`,
`category` (`image` or `video`), registered `renderer`, `output_language`,
`default_variant`, `supported_tasks`, and `capabilities`. `instructions_file`
must be a safe profile-relative Markdown/text path. Optional source metadata
uses `official_documentation`, `official_model_card`, `studio`, `community`, or
`custom`; URLs are metadata only and are never executed or opened automatically.

## Variants and rendering

Variants contain separate required, recommended, and optional positive/negative
prefixes/suffixes, advisory `length_guidance`, reference-only inference
recommendations, provenance, optional target model information, and optional
localized `description` text used by the compact Variant help label. Optional
components are stored separately and remain disabled unless a later UI explicitly
enables them. Quantization is not a variant unless its prompt rules differ.

The renderer assembles fixed profile components deterministically around model
output. Phase 2A-5 registers one renderer per builtin model: `minimax_h3`,
`wan_2_2`, `ltx_2_3`, `krea_2`, and `anima`. Profile selection chooses the
renderer internally; there is no renderer-selection UI. Unknown renderer IDs
fail closed. Krea 2 uses a single natural-language positive prompt and never
invents a negative prompt. Anima detects Natural, Tag, or Hybrid input. Natural
and Tag use their existing JSON contracts; Hybrid uses a dedicated non-JSON
contract while the renderer deterministically preserves the source tag prefix,
applies variant components, and returns separate Positive/Negative outputs.

## Renderer transformation policy

Each model renderer owns its machine-facing Faithful/Balanced/Creative rules,
format, language, structure, length guidance, literal/protected handling,
variant behavior, and Positive/Negative validation. Registry, interfaces,
configuration, history, and LLM transport remain neutral infrastructure. Fixed
components are deterministic. Length guidance never truncates, pads, compresses,
or triggers another LLM pass.

Preferred Literal syntax uses paired markers and supports multiple lines:
`[speech:ja]...[/speech]` and `[text:en]...[/text]`. Legacy line-start markers
remain supported and own the rest of that line. Explicit markers take priority
over legacy syntax and contextual quote inference. Literal bodies survive
exactly while marker syntax is removed. Protected Terms use a separate backend
API. Neither value is written to ordinary logs.

## Localization and security

UI locale resources and any optional profile locale resources are UTF-8 JSON
with stable ASCII keys. English is the UI fallback. Loading profiles performs no
network access, code import, shell execution, or external persistence.
